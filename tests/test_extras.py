import logging

import pytest

from extras import (add_lineage, check_plausibility, file_sha256, is_in_future, reconcile,
                    tag_with_run_id)
from manifest import build_manifest

CONTACT = {"contactid": "CST-001", "createdon": "2023-02-20T14:15:00Z"}


def order(**overrides):
    """A plausible SalesOrder row for CST-001; fields can be overridden per test."""
    row = {"salesorderid": "ORD-1", "customerid": "CST-001",
           "totalamount": "250.50", "createdon": "2023-06-01T09:15:00Z"}
    row.update(overrides)
    return row


# --- 1. Plausibility -------------------------------------------------------

def test_is_in_future():
    assert is_in_future("2999-01-01T00:00:00Z")
    assert not is_in_future("2023-06-01T09:15:00Z")
    assert not is_in_future("")  # an empty (unparseable) date is never "in the future"


def test_plausible_data_logs_no_warning(caplog):
    check_plausibility([CONTACT], [order()])
    assert caplog.text == ""


@pytest.mark.parametrize("overrides, message", [
    ({"totalamount": "-20.00"}, "negative amount"),
    ({"totalamount": "10000.01"}, "unusually large amount"),
    ({"createdon": "2999-01-01T00:00:00Z"}, "order date 2999-01-01T00:00:00Z is in the future"),
    ({"createdon": "2023-01-01T00:00:00Z"}, "before the customer signed up"),
])
def test_suspicious_orders_are_logged(caplog, overrides, message):
    check_plausibility([CONTACT], [order(**overrides)])
    assert message in caplog.text


def test_amount_at_threshold_is_not_logged(caplog):
    check_plausibility([CONTACT], [order(totalamount="10000.00")])
    assert "unusually large" not in caplog.text


def test_future_signup_is_logged(caplog):
    check_plausibility([{"contactid": "CST-001", "createdon": "2999-01-01T00:00:00Z"}], [])
    assert "signup date 2999-01-01T00:00:00Z is in the future" in caplog.text


def test_contact_without_signup_date_is_not_compared(caplog):
    check_plausibility([{"contactid": "CST-001", "createdon": ""}], [order()])
    assert caplog.text == ""


# --- 2. Reconciliation -----------------------------------------------------

def write_raw(tmp_path, text):
    path = tmp_path / "raw.csv"
    path.write_text("id,name,status\n" + text, encoding="utf-8")
    return path


def test_reconcile_passes_when_all_records_are_accounted_for(tmp_path, caplog):
    # 5 records: e.g. 2 loaded + 1 quarantined + 1 joined + 1 duplicate dropped
    path = write_raw(tmp_path, "a\nb\nc\n\nd\ne\n")  # the blank line is not a record
    caplog.set_level(logging.INFO)
    reconcile(path, loaded=2, quarantined=1, lines_joined=1, duplicates_dropped=1)
    assert "reconciliation OK" in caplog.text


def test_reconcile_fails_when_a_record_is_missing(tmp_path):
    path = write_raw(tmp_path, "a\nb\nc\n")
    with pytest.raises(ValueError, match="reconciliation failed"):
        reconcile(path, loaded=2, quarantined=0, lines_joined=0, duplicates_dropped=0)


# --- 3. Traceability -------------------------------------------------------

def test_file_sha256_changes_when_the_file_changes(tmp_path):
    path = tmp_path / "raw.csv"
    path.write_text("a\n", encoding="utf-8")
    first = file_sha256(path)
    path.write_text("b\n", encoding="utf-8")
    assert len(first) == 64
    assert file_sha256(path) != first


def test_add_lineage_uses_standard_model_json_fields():
    manifest = add_lineage(build_manifest(), "20260101T000000Z", "2026-01-01T00:00:00Z")

    assert manifest["application"] == "cdm-ingestion-pipeline"
    assert manifest["modifiedTime"] == "2026-01-01T00:00:00Z"
    assert manifest["annotations"] == [{"name": "pipeline:runId", "value": "20260101T000000Z"}]
    for entity in manifest["entities"]:
        assert entity["partitions"][0]["refreshTime"] == "2026-01-01T00:00:00Z"


def test_add_lineage_places_fields_next_to_related_metadata():
    manifest = add_lineage(build_manifest(), "20260101T000000Z", "2026-01-01T00:00:00Z")

    assert list(manifest) == ["name", "description", "version", "application", "modifiedTime",
                              "annotations", "entities", "relationships"]
    partition = manifest["entities"][0]["partitions"][0]
    assert list(partition) == ["name", "refreshTime", "location", "fileFormatSettings"]


def test_add_lineage_leaves_the_original_manifest_unchanged():
    original = build_manifest()
    add_lineage(original, "20260101T000000Z", "2026-01-01T00:00:00Z")
    assert original == build_manifest()


def test_tag_with_run_id_puts_run_id_first():
    tagged = tag_with_run_id([{"line_number": 6, "reason": "x", "values": "y"}], "RUN")
    assert list(tagged[0]) == ["run_id", "line_number", "reason", "values"]
    assert tagged[0]["run_id"] == "RUN"
