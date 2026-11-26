from decimal import Decimal
from pathlib import Path

import pytest

from transform_orders import parse_amount, transform_orders

RAW_ORDERS = Path(__file__).parent.parent / "data" / "raw" / "raw_orders.csv"
KNOWN_CUSTOMERS = {"CST-001", "CST-002"}


def run(tmp_path, *lines):
    """Write a small raw_orders.csv, transform it and return (orders, rejected)."""
    path = tmp_path / "raw_orders.csv"
    path.write_text("tx_id,customer_id,amount_usd,tx_timestamp\n" + "\n".join(lines) + "\n",
                    encoding="utf-8")
    orders, rejected, _ = transform_orders(path, KNOWN_CUSTOMERS)
    return orders, rejected


# --- parse_amount ----------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("250.50", Decimal("250.50")),
    ("1200.00", Decimal("1200.00")),
    ("-15.00", Decimal("-15.00")),
    ("0", Decimal("0")),
])
def test_valid_amounts(value, expected):
    assert parse_amount(value) == expected


@pytest.mark.parametrize("value", ["", "abc", "1,200.00", "NaN", "Infinity", "12.5 USD"])
def test_invalid_amounts(value):
    assert parse_amount(value) is None


# --- transform_orders ------------------------------------------------------

def test_clean_order_is_mapped(tmp_path):
    orders, rejected = run(tmp_path, "ORD-1,CST-001,250.50,2023-06-01T09:15:00Z")
    assert orders == [{
        "salesorderid": "ORD-1",
        "customerid": "CST-001",
        "totalamount": "250.50",
        "createdon": "2023-06-01T09:15:00Z",
    }]
    assert rejected == []


def test_orphan_is_quarantined(tmp_path):
    orders, rejected = run(tmp_path, "ORD-1,CST-999,99.00,2023-06-06T10:00:00Z")
    assert orders == []
    assert rejected == [{
        "line_number": 2,
        "reason": "orphan: customer 'CST-999' not among loaded contacts",
        "values": "ORD-1,CST-999,99.00,2023-06-06T10:00:00Z",
    }]


def test_invalid_amount_is_quarantined(tmp_path):
    orders, rejected = run(tmp_path, "ORD-1,CST-001,abc,2023-06-01T09:15:00Z")
    assert orders == []
    assert rejected[0]["reason"] == "invalid amount"


def test_invalid_timestamp_is_quarantined(tmp_path):
    orders, rejected = run(tmp_path, "ORD-1,CST-001,10.00,yesterday")
    assert orders == []
    assert rejected[0]["reason"] == "invalid timestamp"


def test_negative_amount_is_loaded(tmp_path):
    orders, _ = run(tmp_path, "ORD-1,CST-001,-20.00,2023-06-01T09:15:00Z")
    assert orders[0]["totalamount"] == "-20.00"


def test_timestamp_without_timezone_is_assumed_utc_and_logged(tmp_path, caplog):
    orders, _ = run(tmp_path, "ORD-1,CST-001,10.00,2023-06-01 09:15:00")
    assert orders[0]["createdon"] == "2023-06-01T09:15:00Z"
    assert "ORD-1" in caplog.text


def test_duplicate_order_ids(tmp_path):
    orders, rejected = run(
        tmp_path,
        "ORD-1,CST-001,10.00,2023-06-01T09:15:00Z",
        "ORD-1,CST-001,10.00,2023-06-01T09:15:00Z",  # exact duplicate -> kept once
        "ORD-2,CST-001,10.00,2023-06-01T09:15:00Z",
        "ORD-2,CST-002,99.00,2023-06-01T09:15:00Z",  # conflicting -> both quarantined
    )
    assert [o["salesorderid"] for o in orders] == ["ORD-1"]
    assert [(r["line_number"], r["reason"]) for r in rejected] == [
        (4, "conflicting duplicate id"),
        (5, "conflicting duplicate id"),
    ]


# --- transform_orders on the real mock file --------------------------------

def test_mock_file():
    contact_ids = {"CST-001", "CST-002", "CST-003", "CST-004", "CST-005"}
    orders, rejected, stats = transform_orders(RAW_ORDERS, contact_ids)

    assert [o["salesorderid"] for o in orders] == ["ORD-1001", "ORD-1002", "ORD-1003", "ORD-1004"]
    assert [(r["line_number"], r["reason"]) for r in rejected] == [
        (6, "orphan: customer 'CST-999' not among loaded contacts"),
    ]
    assert stats == {"lines_joined": 0, "duplicates_dropped": 0}
