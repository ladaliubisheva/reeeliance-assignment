"""Optional extras beyond the assignment's requirements.

They run by default and are switched off with `main.py --core-only`:
1. Plausibility checks - warnings for valid but suspicious values (rows are always loaded).
2. Reconciliation      - proves that no record was lost or created silently.
3. Traceability        - run ID, input checksums, run history, lineage fields in model.json.

None of these change which rows end up in the lakehouse.
"""

import csv
import hashlib
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from utils import OUTPUT_TIMESTAMP_FORMAT

logger = logging.getLogger("extras")


# --- 1. Plausibility checks ------------------------------------------------

# Orders above this amount (USD) are logged as suspicious.
# The value is an example; in a real project it would come from the business.
LARGE_AMOUNT_THRESHOLD = Decimal("10000")


def is_in_future(iso_timestamp):
    """Return True if an ISO timestamp (YYYY-MM-DDTHH:MM:SSZ) lies after now.

    Timestamps in this fixed format can be compared as strings. "" is never in the future.
    """
    now = datetime.now(timezone.utc).strftime(OUTPUT_TIMESTAMP_FORMAT)
    return iso_timestamp > now


def check_plausibility(contacts, orders):
    """Log a warning for every valid but suspicious value. Nothing is changed or removed."""
    signup_dates = {contact["contactid"]: contact["createdon"] for contact in contacts}

    for contact in contacts:
        if is_in_future(contact["createdon"]):
            logger.warning("%s: signup date %s is in the future",
                           contact["contactid"], contact["createdon"])

    for order in orders:
        order_id = order["salesorderid"]
        amount = Decimal(order["totalamount"])
        created_on = order["createdon"]
        signup_date = signup_dates[order["customerid"]]

        if amount < 0:
            logger.warning("%s: negative amount %s (e.g. a refund)", order_id, amount)
        if amount > LARGE_AMOUNT_THRESHOLD:
            logger.warning("%s: unusually large amount %s (above %s)", order_id, amount,
                           LARGE_AMOUNT_THRESHOLD)
        if is_in_future(created_on):
            logger.warning("%s: order date %s is in the future", order_id, created_on)
        if signup_date and created_on < signup_date:
            logger.warning("%s: order date %s is before the customer signed up (%s)",
                           order_id, created_on, signup_date)


# --- 2. Reconciliation -----------------------------------------------------

def count_records(path):
    """Count the non-empty CSV records after the header.

    Deliberately independent of read_raw_csv: reconciliation must not trust the
    code it is checking.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        return sum(1 for record in csv.reader(f) if record) - 1


def reconcile(path, loaded, quarantined, lines_joined, duplicates_dropped):
    """Check that every record of the raw file is accounted for.

    Raises ValueError if records went missing (or appeared) somewhere in the pipeline.
    """
    records = count_records(path)
    accounted = loaded + quarantined + lines_joined + duplicates_dropped
    summary = (f"{Path(path).name}: {records} records = {loaded} loaded + {quarantined} quarantined"
               f" + {lines_joined} joined + {duplicates_dropped} duplicates dropped")
    if records != accounted:
        raise ValueError(f"reconciliation failed for {summary} (sum is {accounted})")
    logger.info("reconciliation OK for %s", summary)


# --- 3. Traceability -------------------------------------------------------

def file_sha256(path):
    """SHA-256 checksum of a file. Proves exactly which version of an input a run processed."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def add_lineage(manifest, run_id, run_time):
    """Return a copy of a model.json dict with lineage, using standard model.json fields:
    application, modifiedTime and annotations on the model, refreshTime on each partition.

    The fields are placed next to related metadata so the file reads naturally:
    model metadata first, refreshTime right after the partition name.
    """
    entities = []
    for entity in manifest["entities"]:
        partitions = [{"name": partition["name"], "refreshTime": run_time, **partition}
                      for partition in entity["partitions"]]
        entities.append({**entity, "partitions": partitions})

    return {
        "name": manifest["name"],
        "description": manifest["description"],
        "version": manifest["version"],
        "application": "cdm-ingestion-pipeline",
        "modifiedTime": run_time,
        "annotations": [{"name": "pipeline:runId", "value": run_id}],
        "entities": entities,
        "relationships": manifest["relationships"],
    }


def tag_with_run_id(rejected, run_id):
    """Add the run ID as first column to quarantined rows."""
    return [{"run_id": run_id, **row} for row in rejected]


def append_run_history(path, run):
    """Append one run summary as a JSON line. Earlier runs are never overwritten."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(run) + "\n")
