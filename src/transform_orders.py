"""Transform raw_orders.csv into rows of the CDM SalesOrder entity."""

import logging
import re
from decimal import Decimal

from utils import check_primary_keys, make_rejected, parse_timestamp, read_raw_csv

logger = logging.getLogger("orders")

RAW_COLUMNS = ["tx_id", "customer_id", "amount_usd", "tx_timestamp"]
SALESORDER_COLUMNS = ["salesorderid", "customerid", "totalamount", "createdon"]

# e.g. "250.50", "-15", "+3.5"
AMOUNT_PATTERN = re.compile(r"[+-]?[0-9]+(\.[0-9]+)?")


def parse_amount(value):
    """Parse an amount as an exact Decimal. Returns None if it is not a plain decimal number.

    Only an optional sign, ASCII digits and an optional decimal part are accepted.
    Decimal() alone would also accept "1_000", "1e3", "NaN" or non-ASCII digits.
    """
    if not AMOUNT_PATTERN.fullmatch(value):
        return None
    return Decimal(value)


def transform_orders(raw_path, contact_ids):
    """Read raw_orders.csv and return (orders, rejected, stats).

    contact_ids is the set of contactids that were loaded; orders pointing to any other
    customer are orphans and get quarantined. Orders with an invalid amount or timestamp
    are quarantined too, because these are core fields of an order.
    stats counts rows that were neither loaded nor quarantined (needed for reconciliation).
    """
    raw_rows, rejected, lines_joined = read_raw_csv(raw_path, RAW_COLUMNS)
    raw_rows, key_rejected, duplicates_dropped = check_primary_keys(raw_rows, "tx_id")
    rejected += key_rejected

    orders = []
    for line_number, raw in raw_rows:
        order_id = raw["tx_id"]
        customer_id = raw["customer_id"]

        if customer_id not in contact_ids:
            reason = f"orphan: customer {customer_id!r} not among loaded contacts"
            logger.error("%s: %s - quarantined", order_id, reason)
            rejected.append(make_rejected(line_number, reason, raw.values()))
            continue

        amount = parse_amount(raw["amount_usd"])
        if amount is None:
            logger.error("%s: invalid amount %r - quarantined", order_id, raw["amount_usd"])
            rejected.append(make_rejected(line_number, "invalid amount", raw.values()))
            continue

        created_on, warning = parse_timestamp(raw["tx_timestamp"])
        if not created_on:
            logger.error("%s: invalid tx_timestamp %r - quarantined", order_id, raw["tx_timestamp"])
            rejected.append(make_rejected(line_number, "invalid timestamp", raw.values()))
            continue
        if warning:
            logger.warning("%s: tx_timestamp %r %s", order_id, raw["tx_timestamp"], warning)

        orders.append({
            "salesorderid": order_id,
            "customerid": customer_id,
            "totalamount": format(amount, "f"),  # plain notation, keeps "250.50" as written
            "createdon": created_on,
        })

    rejected.sort(key=lambda r: r["line_number"])
    logger.info("%d orders transformed, %d rows quarantined", len(orders), len(rejected))
    stats = {"lines_joined": lines_joined, "duplicates_dropped": duplicates_dropped}
    return orders, rejected, stats
