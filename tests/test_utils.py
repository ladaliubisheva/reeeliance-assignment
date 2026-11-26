import csv
from datetime import timedelta, timezone

import pytest

import utils
from utils import check_primary_keys, is_valid_email, parse_timestamp, read_raw_csv, write_csv


# --- parse_timestamp -------------------------------------------------------

def test_timestamp_already_utc_is_kept_without_warning():
    assert parse_timestamp("2023-01-15T10:30:00Z") == ("2023-01-15T10:30:00Z", None)


def test_timestamp_without_timezone_is_assumed_utc():
    result, warning = parse_timestamp("2023-02-20 14:15:00")
    assert result == "2023-02-20T14:15:00Z"
    assert "assumed UTC" in warning


def test_timestamp_with_t_but_without_timezone_is_assumed_utc():
    result, warning = parse_timestamp("2023-02-20T14:15:00")
    assert result == "2023-02-20T14:15:00Z"
    assert "assumed UTC" in warning


def test_date_only_gets_midnight():
    result, warning = parse_timestamp("2023-03-05")
    assert result == "2023-03-05T00:00:00Z"
    assert "00:00:00" in warning


def test_changing_source_timezone_only_affects_values_without_timezone(monkeypatch):
    # Pretend the source system runs on UTC+1.
    monkeypatch.setattr(utils, "SOURCE_TIMEZONE", timezone(timedelta(hours=1)))

    # Values ending in "Z" are already UTC and must not change ...
    assert parse_timestamp("2023-01-15T10:30:00Z")[0] == "2023-01-15T10:30:00Z"
    # ... values without a timezone are converted from UTC+1 to UTC.
    result, warning = parse_timestamp("2023-02-20 14:15:00")
    assert result == "2023-02-20T13:15:00Z"
    assert "UTC+01:00" in warning


@pytest.mark.parametrize("value", ["", "not a date", "2023-13-01", "05.03.2023"])
def test_unparseable_timestamp_becomes_empty(value):
    result, warning = parse_timestamp(value)
    assert result == ""
    assert "could not be parsed" in warning


# --- is_valid_email --------------------------------------------------------

@pytest.mark.parametrize("value", ["jane.doe@example.com", "john.smith@domain.com", "a@b.co"])
def test_valid_emails(value):
    assert is_valid_email(value)


@pytest.mark.parametrize("value", [
    "invalid-email-format",
    "",
    "no-at-sign.com",
    "two@@example.com",
    "missing-tld@example",
    "has space@example.com",
])
def test_invalid_emails(value):
    assert not is_valid_email(value)


# --- read_raw_csv / write_csv ----------------------------------------------

COLUMNS = ["id", "name", "status"]


def write_raw(tmp_path, text):
    path = tmp_path / "raw.csv"
    path.write_text("id,name,status\n" + text, encoding="utf-8")
    return path


def test_read_raw_csv_strips_whitespace_and_keeps_line_numbers(tmp_path):
    path = write_raw(tmp_path, "1, Jane ,active\n\n2,Bob,inactive\n")  # includes a blank line

    rows, rejected, joined = read_raw_csv(path, COLUMNS)

    assert rows == [
        (2, {"id": "1", "name": "Jane", "status": "active"}),
        (4, {"id": "2", "name": "Bob", "status": "inactive"}),
    ]
    assert rejected == []


def test_read_raw_csv_repairs_line_split_inside_a_field(tmp_path):
    # Same pattern as CST-005: the name field is followed by a line break.
    path = write_raw(tmp_path, "1, Charlie Brown\n,active\n")

    rows, rejected, joined = read_raw_csv(path, COLUMNS)

    assert rows == [(2, {"id": "1", "name": "Charlie Brown", "status": "active"})]
    assert rejected == []
    assert joined == 1


def test_read_raw_csv_does_not_merge_short_line_into_valid_next_line(tmp_path):
    # "1" + "2,Bob,inactive" would give 3 fields, but that would corrupt a valid row.
    path = write_raw(tmp_path, "1\n2,Bob,inactive\n")

    rows, rejected, joined = read_raw_csv(path, COLUMNS)

    assert rows == [(3, {"id": "2", "name": "Bob", "status": "inactive"})]
    assert rejected == [{"line_number": 2, "reason": "wrong number of fields", "values": "1"}]


def test_rejected_values_keep_fields_with_commas_unambiguous(tmp_path):
    # A quoted field containing a comma must stay one field in the rejected file.
    path = write_raw(tmp_path, '1,"Doe, Jane",active,extra\n')

    _, rejected, _ = read_raw_csv(path, COLUMNS)
    assert rejected[0]["values"] == '1,"Doe, Jane",active,extra'

    # Round trip: write the rejected file and read the original fields back.
    rejected_file = tmp_path / "rejected.csv"
    write_csv(rejected_file, ["line_number", "reason", "values"], rejected)
    with open(rejected_file, newline="", encoding="utf-8") as f:
        stored = next(csv.DictReader(f))["values"]
    assert next(csv.reader([stored])) == ["1", "Doe, Jane", "active", "extra"]


def test_read_raw_csv_quarantines_unrepairable_lines(tmp_path):
    path = write_raw(tmp_path, "1,John,active,extra\n2,Broken\n")  # too many / too few at the end

    rows, rejected, joined = read_raw_csv(path, COLUMNS)

    assert rows == []
    assert [r["line_number"] for r in rejected] == [2, 3]
    assert rejected[0]["values"] == "1,John,active,extra"


def test_read_raw_csv_accepts_file_with_bom(tmp_path):
    path = tmp_path / "raw.csv"
    path.write_text("\ufeffid,name,status\n1,Jane,active\n", encoding="utf-8")  # BOM as in Excel exports

    rows, _, _ = read_raw_csv(path, COLUMNS)

    assert rows == [(2, {"id": "1", "name": "Jane", "status": "active"})]


def test_read_raw_csv_rejects_empty_file(tmp_path):
    path = tmp_path / "raw.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="file is empty"):
        read_raw_csv(path, COLUMNS)


def test_read_raw_csv_rejects_unexpected_header(tmp_path):
    path = tmp_path / "raw.csv"
    path.write_text("wrong,header\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_raw_csv(path, ["id", "name"])


# --- check_primary_keys ----------------------------------------------------

def test_unique_keys_are_kept():
    rows = [(2, {"id": "1", "name": "Jane"}), (3, {"id": "2", "name": "Bob"})]
    assert check_primary_keys(rows, "id") == (rows, [], 0)


def test_empty_key_is_quarantined():
    kept, rejected, dropped = check_primary_keys([(2, {"id": "", "name": "Jane"})], "id")
    assert kept == []
    assert rejected == [{"line_number": 2, "reason": "empty primary key", "values": ",Jane"}]


def test_exact_duplicate_is_kept_once(caplog):
    rows = [(2, {"id": "1", "name": "Jane"}), (3, {"id": "1", "name": "Jane"})]
    kept, rejected, dropped = check_primary_keys(rows, "id")
    assert kept == [(2, {"id": "1", "name": "Jane"})]
    assert rejected == []
    assert dropped == 1
    assert "identical data" in caplog.text


def test_conflicting_duplicates_are_all_quarantined():
    rows = [
        (2, {"id": "1", "name": "Jane"}),
        (3, {"id": "2", "name": "Bob"}),
        (4, {"id": "1", "name": "Janet"}),
    ]
    kept, rejected, dropped = check_primary_keys(rows, "id")
    assert kept == [(3, {"id": "2", "name": "Bob"})]
    assert [(r["line_number"], r["reason"]) for r in rejected] == [
        (2, "conflicting duplicate id"),
        (4, "conflicting duplicate id"),
    ]


def test_write_csv_creates_folders_and_header(tmp_path):
    path = tmp_path / "Contact" / "Contact.csv"
    write_csv(path, ["a", "b"], [{"a": "1", "b": "2"}])
    assert path.read_text(encoding="utf-8").splitlines() == ["a,b", "1,2"]
