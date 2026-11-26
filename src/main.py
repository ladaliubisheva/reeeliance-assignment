"""Run the whole pipeline: raw CSVs -> CDM entities -> lakehouse files + model.json.

Usage (from the project root):
    .venv\\Scripts\\python.exe src\\main.py              # everything (default)
    .venv\\Scripts\\python.exe src\\main.py --core-only  # only what the assignment requires
"""

import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

import extras
from manifest import build_manifest, write_manifest
from transform_contacts import CONTACT_COLUMNS, transform_contacts
from transform_orders import SALESORDER_COLUMNS, transform_orders
from utils import OUTPUT_TIMESTAMP_FORMAT, REJECTED_COLUMNS, setup_logging, write_csv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CUSTOMERS_FILE = "raw_customers.csv"
ORDERS_FILE = "raw_orders.csv"

logger = logging.getLogger("pipeline")


def utc_now():
    return datetime.now(timezone.utc).strftime(OUTPUT_TIMESTAMP_FORMAT)


def write_rejected(path, columns, rejected):
    """Write the quarantine file, or remove an old one if nothing was rejected this run."""
    if rejected:
        write_csv(path, columns, rejected)
    else:
        path.unlink(missing_ok=True)


def run_pipeline(raw_dir, lakehouse_dir, rejected_dir, with_extras=True, run_id=None, run_time=None):
    """Transform and write everything. Returns the record counts of the run.

    with_extras=False runs only what the assignment requires; otherwise run_id and run_time
    are needed for the traceability extras.
    """
    # 1. Transform (core).
    contacts, rejected_contacts, contact_stats = transform_contacts(raw_dir / CUSTOMERS_FILE)
    contact_ids = {contact["contactid"] for contact in contacts}
    orders, rejected_orders, order_stats = transform_orders(raw_dir / ORDERS_FILE, contact_ids)

    manifest = build_manifest()
    rejected_columns = REJECTED_COLUMNS

    # 2. Extras. Reconciliation raises ValueError before anything is written.
    if with_extras:
        extras.check_plausibility(contacts, orders)
        extras.reconcile(raw_dir / CUSTOMERS_FILE, len(contacts), len(rejected_contacts),
                         **contact_stats)
        extras.reconcile(raw_dir / ORDERS_FILE, len(orders), len(rejected_orders), **order_stats)
        manifest = extras.add_lineage(manifest, run_id, run_time)
        rejected_contacts = extras.tag_with_run_id(rejected_contacts, run_id)
        rejected_orders = extras.tag_with_run_id(rejected_orders, run_id)
        rejected_columns = ["run_id"] + REJECTED_COLUMNS

    # 3. Write outputs (core).
    write_csv(lakehouse_dir / "Contact" / "Contact.csv", CONTACT_COLUMNS, contacts)
    write_csv(lakehouse_dir / "SalesOrder" / "SalesOrder.csv", SALESORDER_COLUMNS, orders)
    write_manifest(lakehouse_dir / "model.json", manifest)
    write_rejected(rejected_dir / "raw_customers_rejected.csv", rejected_columns, rejected_contacts)
    write_rejected(rejected_dir / "raw_orders_rejected.csv", rejected_columns, rejected_orders)

    counts = {
        "contacts_loaded": len(contacts),
        "contacts_quarantined": len(rejected_contacts),
        "orders_loaded": len(orders),
        "orders_quarantined": len(rejected_orders),
    }
    logger.info("done: %s", counts)
    return counts


def main(root=PROJECT_ROOT, with_extras=True):
    started_at = utc_now()
    run_id = started_at.replace("-", "").replace(":", "")  # e.g. 20261126T161557Z
    raw_dir = root / "data" / "raw"

    log_name = f"pipeline_{run_id}.log" if with_extras else "pipeline.log"
    setup_logging(root / "logs" / log_name)
    logger.info("run %s started%s", run_id, "" if with_extras else " (core only)")

    inputs = {}
    run = {"status": "success"}
    try:
        if with_extras:  # checksums are taken before processing starts
            inputs = {name: {"sha256": extras.file_sha256(raw_dir / name)}
                      for name in [CUSTOMERS_FILE, ORDERS_FILE]}
        run["counts"] = run_pipeline(raw_dir, root / "cdm_lakehouse", root / "data" / "rejected",
                                     with_extras, run_id, started_at)
    except (ValueError, OSError) as error:  # bad/missing input file or failed reconciliation
        logger.error("pipeline stopped: %s", error)
        run = {"status": "stopped", "error": str(error)}

    if with_extras:
        extras.append_run_history(root / "logs" / "run_history.jsonl", {
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": utc_now(),
            "inputs": inputs,
            **run,
        })

    if run["status"] != "success":
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CDM ingestion pipeline")
    parser.add_argument("--core-only", action="store_true",
                        help="run only what the assignment requires: no plausibility checks, "
                             "no reconciliation, no run history or lineage fields")
    args = parser.parse_args()
    main(with_extras=not args.core_only)
