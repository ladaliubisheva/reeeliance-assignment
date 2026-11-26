"""Transform raw_customers.csv into rows of the CDM Contact entity."""

import logging

from nameparser import parse

from utils import check_primary_keys, is_valid_email, parse_timestamp, read_raw_csv

logger = logging.getLogger("contacts")

RAW_COLUMNS = ["cust_id", "full_name", "email_addr", "signup_date", "user_status"]
CONTACT_COLUMNS = ["contactid", "firstname", "lastname", "emailaddress1", "createdon", "statecode"]

STATE_CODES = {"active": 0, "inactive": 1}


def split_name(full_name):
    """Split a full name into (firstname, lastname) using nameparser.

    - Middle names are kept as part of the first name ("Mary Ann Smith" -> "Mary Ann", "Smith").
    - Titles and suffixes ("Dr.", "Jr.") are dropped.
    - A single-word name goes into lastname, because lastname is the required field in CDM.
    """
    parsed = parse(full_name)
    firstname = " ".join(part for part in [parsed.given, parsed.middle] if part)
    lastname = parsed.family

    if firstname and not lastname:
        firstname, lastname = "", firstname
    return firstname, lastname


def transform_contact(raw):
    """Turn one raw customer row (dict) into one Contact row (dict). Problems are logged."""
    contact_id = raw["cust_id"]

    firstname, lastname = split_name(raw["full_name"])
    if not lastname:
        logger.warning("%s: name is empty", contact_id)
    elif not firstname:
        logger.warning("%s: single-word name %r - stored as lastname", contact_id, lastname)

    email = raw["email_addr"].lower()
    if not is_valid_email(email):
        logger.warning("%s: invalid email %r - set to empty", contact_id, raw["email_addr"])
        email = ""

    created_on, warning = parse_timestamp(raw["signup_date"])
    if warning:
        logger.warning("%s: signup_date %r %s", contact_id, raw["signup_date"], warning)

    status = raw["user_status"].lower()
    state_code = STATE_CODES.get(status, "")
    if state_code == "":
        logger.warning("%s: unknown user_status %r - set to empty", contact_id, raw["user_status"])

    return {
        "contactid": contact_id,
        "firstname": firstname,
        "lastname": lastname,
        "emailaddress1": email,
        "createdon": created_on,
        "statecode": state_code,
    }


def transform_contacts(raw_path):
    """Read raw_customers.csv and return (contacts, rejected, stats).

    - rejected contains the quarantined rows; the caller writes them to the rejected file.
    - stats counts rows that were neither loaded nor quarantined (needed for reconciliation).
    """
    raw_rows, rejected, lines_joined = read_raw_csv(raw_path, RAW_COLUMNS)
    raw_rows, key_rejected, duplicates_dropped = check_primary_keys(raw_rows, "cust_id")
    rejected += key_rejected

    contacts = [transform_contact(raw) for _, raw in raw_rows]

    rejected.sort(key=lambda r: r["line_number"])
    logger.info("%d contacts transformed, %d rows quarantined", len(contacts), len(rejected))
    stats = {"lines_joined": lines_joined, "duplicates_dropped": duplicates_dropped}
    return contacts, rejected, stats
