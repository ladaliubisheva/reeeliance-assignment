"""Shared helpers: logging, CSV reading/writing, data quality checks, timestamps, emails."""

import csv
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("quality")

# Timezone assumed for timestamps that come without one.
# Evidence: all other timestamps in the sources end in "Z" (UTC).
# If a source system turns out to use local time, change it here, e.g.
# ZoneInfo("Europe/Berlin") (on Windows this needs the "tzdata" package).
# Values that end in "Z" are always UTC and are not affected by this setting.
SOURCE_TIMEZONE = timezone.utc

OUTPUT_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# Accepted input formats, each with the warning to log when it is used.
# None means the value is complete and nothing had to be assumed.
# {tz} is replaced by SOURCE_TIMEZONE in the message.
INPUT_TIMESTAMP_FORMATS = [
    ("%Y-%m-%dT%H:%M:%SZ", None),
    ("%Y-%m-%d %H:%M:%S", "has no timezone - assumed {tz}"),
    ("%Y-%m-%dT%H:%M:%S", "has no timezone - assumed {tz}"),
    ("%Y-%m-%d", "has no time and no timezone - assumed 00:00:00 {tz}"),
]

# Simple check: something@something.tld, no spaces, exactly one "@".
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Columns of the quarantine files in data/rejected/.
REJECTED_COLUMNS = ["line_number", "reason", "values"]


def setup_logging(log_file):
    """Log to the console and to a file. An existing file with the same name is overwritten."""
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, mode="w", encoding="utf-8"),
        ],
    )


def make_rejected(line_number, reason, values):
    """Build one row for a quarantine file."""
    return {"line_number": line_number, "reason": reason, "values": ",".join(values)}


def clean_row(columns, values):
    """Turn a list of values into a dict and strip surrounding whitespace."""
    return dict(zip(columns, [value.strip() for value in values]))


def read_raw_csv(path, expected_columns):
    """Read a raw CSV file, repairing broken lines where this is unambiguous.

    Returns (rows, rejected, lines_joined):
    - rows: list of (line_number, row_dict); whitespace is stripped from every value.
    - rejected: quarantine rows (see make_rejected) for lines that could not be used.
    - lines_joined: how many repairs were made (each repair turns two lines into one row).

    Repair rule: if a line has too few fields, and the next line also has too few fields,
    and joining them gives exactly the expected number of fields, they are treated as one
    record whose text was split by a line break (e.g. CST-005 in raw_customers.csv).
    """
    file_name = Path(path).name
    # "utf-8-sig" also accepts files that start with a BOM (common in Excel exports).
    with open(path, newline="", encoding="utf-8-sig") as f:
        lines = list(csv.reader(f))

    header = lines[0]
    if header != expected_columns:
        raise ValueError(f"{path}: expected columns {expected_columns}, got {header}")

    expected_count = len(expected_columns)
    rows = []
    rejected = []
    lines_joined = 0
    i = 1
    while i < len(lines):
        values = lines[i]
        line_number = i + 1  # the header is line 1

        if not values:  # blank line
            i += 1
            continue

        if len(values) == expected_count:
            rows.append((line_number, clean_row(expected_columns, values)))
            i += 1
            continue

        next_values = lines[i + 1] if i + 1 < len(lines) else []
        if len(values) < expected_count and 0 < len(next_values) < expected_count:
            # The last field of this line continues in the first field of the next line.
            merged = values[:-1] + [values[-1] + next_values[0]] + next_values[1:]
            if len(merged) == expected_count:
                logger.warning("%s: lines %d-%d were split by a line break - joined into one record",
                               file_name, line_number, line_number + 1)
                rows.append((line_number, clean_row(expected_columns, merged)))
                lines_joined += 1
                i += 2
                continue

        logger.error("%s: line %d has %d fields instead of %d - quarantined",
                     file_name, line_number, len(values), expected_count)
        rejected.append(make_rejected(line_number, "wrong number of fields", values))
        i += 1

    return rows, rejected, lines_joined


def check_primary_keys(rows, key_column):
    """Make sure every row has a unique, non-empty primary key.

    rows is a list of (line_number, row_dict). Returns (kept_rows, rejected, duplicates_dropped):
    - empty key                         -> quarantined
    - exact duplicates (identical rows)  -> one copy kept, warning logged
    - same key with different data       -> all copies quarantined
    """
    rows_by_key = {}
    for line_number, row in rows:
        rows_by_key.setdefault(row[key_column], []).append((line_number, row))

    kept = []
    rejected = []
    duplicates_dropped = 0
    for key, copies in rows_by_key.items():
        line_numbers = [line_number for line_number, _ in copies]

        if key == "":
            logger.error("empty %s on line(s) %s - quarantined", key_column, line_numbers)
            reason = "empty primary key"
        elif all(row == copies[0][1] for _, row in copies):
            if len(copies) > 1:
                logger.warning("%s %s appears %d times with identical data (lines %s) - kept once",
                               key_column, key, len(copies), line_numbers)
            kept.append(copies[0])
            duplicates_dropped += len(copies) - 1
            continue
        else:
            logger.error("%s %s appears with different data (lines %s) - all copies quarantined",
                         key_column, key, line_numbers)
            reason = "conflicting duplicate id"

        for line_number, row in copies:
            rejected.append(make_rejected(line_number, reason, row.values()))

    return kept, rejected, duplicates_dropped


def write_csv(path, columns, rows):
    """Write a list of dicts to a CSV file with a header. Creates folders if needed."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def parse_timestamp(value):
    """Normalize a timestamp to ISO 8601 UTC (YYYY-MM-DDTHH:MM:SSZ).

    Returns (iso_string, warning):
    - iso_string is "" if the value could not be parsed.
    - warning is None if nothing had to be assumed, otherwise a short message
      that the caller should log (together with the record id).
    """
    for input_format, warning in INPUT_TIMESTAMP_FORMATS:
        try:
            parsed = datetime.strptime(value, input_format)
        except ValueError:
            continue

        if warning is None:
            # Ends in "Z": the value is already UTC.
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            # No timezone in the value: interpret it in SOURCE_TIMEZONE, then convert to UTC.
            parsed = parsed.replace(tzinfo=SOURCE_TIMEZONE).astimezone(timezone.utc)
            warning = warning.format(tz=SOURCE_TIMEZONE)
        return parsed.strftime(OUTPUT_TIMESTAMP_FORMAT), warning

    return "", "could not be parsed"


def is_valid_email(value):
    """Return True if the value looks like a valid email address."""
    return bool(EMAIL_PATTERN.match(value))
