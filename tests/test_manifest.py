from manifest import build_manifest
from transform_contacts import CONTACT_COLUMNS
from transform_orders import SALESORDER_COLUMNS

# The "Expected model.json Structure Reference" from the assignment PDF, copied as-is.
PDF_REFERENCE = {
    "name": "EnterpriseDataLake",
    "description": "CDM Manifest for Ingested Customer and Sales Data",
    "version": "1.0",
    "entities": [
        {
            "$type": "LocalEntity",
            "name": "Contact",
            "description": "Standard CDM Contact entity for individual customers",
            "attributes": [
                {"name": "contactid", "dataType": "string", "description": "Unique primary key for the contact"},
                {"name": "firstname", "dataType": "string", "description": "First name of the contact"},
                {"name": "lastname", "dataType": "string", "description": "Last name of the contact"},
                {"name": "emailaddress1", "dataType": "string", "description": "Primary email address"},
                {"name": "createdon", "dataType": "dateTime", "description": "ISO 8601 creation timestamp"},
                {"name": "statecode", "dataType": "int64", "description": "Status code: 0 for Active, 1 for Inactive"},
            ],
            "partitions": [
                {
                    "name": "ContactPartitions",
                    "location": "Contact/Contact.csv",
                    "fileFormatSettings": {"$type": "CsvFormatSettings", "columnHeaders": True, "delimiter": ","},
                }
            ],
        },
        {
            "$type": "LocalEntity",
            "name": "SalesOrder",
            "description": "Standard CDM SalesOrder entity for transactions",
            "attributes": [
                {"name": "salesorderid", "dataType": "string", "description": "Unique order identifier"},
                {"name": "customerid", "dataType": "string", "description": "Foreign key reference to Contact"},
                {"name": "totalamount", "dataType": "decimal", "description": "Total order amount in USD"},
                {"name": "createdon", "dataType": "dateTime", "description": "ISO 8601 transaction timestamp"},
            ],
            "partitions": [
                {
                    "name": "SalesOrderPartitions",
                    "location": "SalesOrder/SalesOrder.csv",
                    "fileFormatSettings": {"$type": "CsvFormatSettings", "columnHeaders": True, "delimiter": ","},
                }
            ],
        },
    ],
    "relationships": [
        {
            "$type": "SingleKeyRelationship",
            "name": "Contact_SalesOrder_Relationship",
            "fromAttribute": {"entityName": "SalesOrder", "attributeName": "customerid"},
            "toAttribute": {"entityName": "Contact", "attributeName": "contactid"},
        }
    ],
}


def test_manifest_matches_the_assignment_reference_exactly():
    assert build_manifest() == PDF_REFERENCE


def test_attributes_match_the_csv_columns():
    contact, sales_order = build_manifest()["entities"]
    assert [a["name"] for a in contact["attributes"]] == CONTACT_COLUMNS
    assert [a["name"] for a in sales_order["attributes"]] == SALESORDER_COLUMNS
