from pathlib import Path

import pytest

from transform_contacts import split_name, transform_contact, transform_contacts

RAW_CUSTOMERS = Path(__file__).parent.parent / "data" / "raw" / "raw_customers.csv"


def raw_row(**overrides):
    """A valid raw customer row; individual fields can be overridden per test."""
    row = {
        "cust_id": "CST-001",
        "full_name": "Jane Doe",
        "email_addr": "jane.doe@example.com",
        "signup_date": "2023-01-15T10:30:00Z",
        "user_status": "active",
    }
    row.update(overrides)
    return row


# --- split_name ------------------------------------------------------------

@pytest.mark.parametrize("full_name, expected", [
    ("Jane Doe", ("Jane", "Doe")),
    ("Mary Ann Smith", ("Mary Ann", "Smith")),           # middle name kept in firstname
    ("Ludwig van Beethoven", ("Ludwig", "van Beethoven")),  # surname particle
    ("Dr. Jane Doe", ("Jane", "Doe")),                   # title dropped
    ("Martin Luther King Jr.", ("Martin Luther", "King")),  # suffix dropped
    ("Doe, Jane", ("Jane", "Doe")),                      # "Last, First" format
    ("  Charlie   Brown ", ("Charlie", "Brown")),        # extra whitespace
    ("Madonna", ("", "Madonna")),                        # single word -> lastname
    ("", ("", "")),
])
def test_split_name(full_name, expected):
    assert split_name(full_name) == expected


# --- transform_contact -----------------------------------------------------

def test_clean_row_is_mapped_to_contact():
    assert transform_contact(raw_row()) == {
        "contactid": "CST-001",
        "firstname": "Jane",
        "lastname": "Doe",
        "emailaddress1": "jane.doe@example.com",
        "createdon": "2023-01-15T10:30:00Z",
        "statecode": 0,
    }


def test_email_is_lowercased():
    contact = transform_contact(raw_row(email_addr="JOHN.SMITH@DOMAIN.COM"))
    assert contact["emailaddress1"] == "john.smith@domain.com"


def test_invalid_email_is_set_to_empty_and_logged(caplog):
    contact = transform_contact(raw_row(email_addr="invalid-email-format"))
    assert contact["emailaddress1"] == ""
    assert "invalid email" in caplog.text


@pytest.mark.parametrize("status, expected", [
    ("active", 0),
    ("ACTIVE", 0),
    ("inactive", 1),
    ("Inactive", 1),
    ("deleted", ""),
    ("", ""),
])
def test_status_mapping(status, expected):
    assert transform_contact(raw_row(user_status=status))["statecode"] == expected


def test_unknown_status_is_logged(caplog):
    transform_contact(raw_row(user_status="deleted"))
    assert "unknown user_status" in caplog.text


def test_timestamp_assumption_is_logged_with_id(caplog):
    contact = transform_contact(raw_row(cust_id="CST-003", signup_date="2023-03-05"))
    assert contact["createdon"] == "2023-03-05T00:00:00Z"
    assert "CST-003" in caplog.text


def test_single_word_name_is_logged(caplog):
    transform_contact(raw_row(full_name="Madonna"))
    assert "single-word name" in caplog.text


# --- transform_contacts on the real mock file ------------------------------

def test_mock_file():
    contacts, rejected, stats = transform_contacts(RAW_CUSTOMERS)

    assert [c["contactid"] for c in contacts] == ["CST-001", "CST-002", "CST-003", "CST-004", "CST-005"]
    assert rejected == []
    assert stats == {"lines_joined": 1, "duplicates_dropped": 0}
    # CST-005 is split over two lines in the raw file and gets repaired
    assert contacts[4] == {
        "contactid": "CST-005",
        "firstname": "Charlie",
        "lastname": "Brown",
        "emailaddress1": "charlie@peanuts.com",
        "createdon": "2023-05-01T12:00:00Z",
        "statecode": 1,
    }
