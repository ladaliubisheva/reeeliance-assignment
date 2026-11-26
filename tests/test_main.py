"""End-to-end tests: run the whole pipeline on the mock data in a temporary folder."""

import csv
import json
import shutil
from pathlib import Path

import pytest

import main
import transform_contacts
from main import run_pipeline
from manifest import build_manifest
from utils import check_primary_keys

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
RUN_ID = "20260101T000000Z"
RUN_TIME = "2026-01-01T00:00:00Z"


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_manifest(tmp_path):
    return json.loads((tmp_path / "cdm_lakehouse" / "model.json").read_text(encoding="utf-8"))


def run(tmp_path, with_extras=True):
    return run_pipeline(RAW_DIR, tmp_path / "cdm_lakehouse", tmp_path / "rejected",
                        with_extras, RUN_ID, RUN_TIME)


def simulate_lost_contact(monkeypatch):
    """Simulate a bug: check_primary_keys silently loses the last contact."""
    def buggy_check(rows, key_column):
        kept, rejected, dropped = check_primary_keys(rows, key_column)
        return kept[:-1], rejected, dropped

    monkeypatch.setattr(transform_contacts, "check_primary_keys", buggy_check)


# --- run_pipeline: core results (identical in both modes) -------------------

@pytest.mark.parametrize("with_extras", [True, False])
def test_pipeline_on_mock_data(tmp_path, with_extras):
    counts = run(tmp_path, with_extras)
    lakehouse = tmp_path / "cdm_lakehouse"

    contacts = read_csv(lakehouse / "Contact" / "Contact.csv")
    orders = read_csv(lakehouse / "SalesOrder" / "SalesOrder.csv")
    assert counts == {"contacts_loaded": 5, "contacts_quarantined": 0,
                      "orders_loaded": 4, "orders_quarantined": 1}

    # Every partition listed in model.json exists.
    for entity in read_manifest(tmp_path)["entities"]:
        assert (lakehouse / entity["partitions"][0]["location"]).exists()

    # Referential integrity: every order points to a loaded contact.
    contact_ids = {c["contactid"] for c in contacts}
    assert all(o["customerid"] in contact_ids for o in orders)

    # Only the orphan order is quarantined.
    [rejected] = read_csv(tmp_path / "rejected" / "raw_orders_rejected.csv")
    assert rejected["values"] == "ORD-1005,CST-999,99.00,2023-06-06T10:00:00Z"
    assert not (tmp_path / "rejected" / "raw_customers_rejected.csv").exists()


def test_old_rejected_file_is_removed_when_nothing_is_rejected(tmp_path):
    rejected = tmp_path / "rejected"
    rejected.mkdir()
    old_file = rejected / "raw_customers_rejected.csv"
    old_file.write_text("leftover from an earlier run\n", encoding="utf-8")

    run(tmp_path)

    assert not old_file.exists()


# --- run_pipeline: differences between the modes ----------------------------

def test_extras_add_lineage_and_run_id(tmp_path):
    run(tmp_path, with_extras=True)

    assert read_manifest(tmp_path)["annotations"] == [{"name": "pipeline:runId", "value": RUN_ID}]
    [rejected] = read_csv(tmp_path / "rejected" / "raw_orders_rejected.csv")
    assert rejected["run_id"] == RUN_ID


def test_core_only_writes_exactly_the_reference_manifest(tmp_path):
    run(tmp_path, with_extras=False)

    assert read_manifest(tmp_path) == build_manifest()
    [rejected] = read_csv(tmp_path / "rejected" / "raw_orders_rejected.csv")
    assert list(rejected) == ["line_number", "reason", "values"]


def test_failed_reconciliation_stops_before_writing(tmp_path, monkeypatch):
    simulate_lost_contact(monkeypatch)

    with pytest.raises(ValueError, match="reconciliation failed"):
        run(tmp_path, with_extras=True)

    assert not (tmp_path / "cdm_lakehouse").exists()


def test_core_only_does_not_reconcile(tmp_path, monkeypatch):
    # Documents the trade-off of --core-only: the same bug goes unnoticed.
    simulate_lost_contact(monkeypatch)

    counts = run(tmp_path, with_extras=False)

    assert counts["contacts_loaded"] == 4


# --- main: run history -----------------------------------------------------

@pytest.fixture
def project(tmp_path, monkeypatch):
    """A temporary project folder with a copy of the mock data. Logging setup is skipped
    so the tests don't leave log handlers attached to temporary files."""
    shutil.copytree(RAW_DIR, tmp_path / "data" / "raw")
    monkeypatch.setattr(main, "setup_logging", lambda log_file: None)
    return tmp_path


def read_history(project):
    lines = (project / "logs" / "run_history.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_successful_run_is_recorded_in_history(project):
    main.main(project)

    [run_record] = read_history(project)
    assert run_record["status"] == "success"
    assert run_record["counts"]["orders_quarantined"] == 1
    assert run_record["started_at"] <= run_record["finished_at"]
    assert len(run_record["inputs"]["raw_customers.csv"]["sha256"]) == 64


def test_stopped_run_is_recorded_and_earlier_runs_are_kept(project):
    main.main(project)
    (project / "data" / "raw" / "raw_customers.csv").write_text("wrong,header\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        main.main(project)

    first, second = read_history(project)
    assert first["status"] == "success"
    assert second["status"] == "stopped"
    assert "expected columns" in second["error"]
    # A changed input file has a different checksum.
    assert first["inputs"]["raw_customers.csv"] != second["inputs"]["raw_customers.csv"]


def test_core_only_writes_no_run_history(project):
    main.main(project, with_extras=False)

    assert (project / "cdm_lakehouse" / "model.json").exists()
    assert not (project / "logs" / "run_history.jsonl").exists()
