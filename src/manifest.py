"""Build the Microsoft CDM model.json manifest for the lakehouse."""

import json

from transform_contacts import CONTACT_COLUMNS
from transform_orders import SALESORDER_COLUMNS

# dataType and description for every attribute. The attribute list itself (names and order)
# comes from the column lists the transforms write, so CSV files and manifest can't drift apart.
ATTRIBUTE_DETAILS = {
    "Contact": {
        "contactid": ("string", "Unique primary key for the contact"),
        "firstname": ("string", "First name of the contact"),
        "lastname": ("string", "Last name of the contact"),
        "emailaddress1": ("string", "Primary email address"),
        "createdon": ("dateTime", "ISO 8601 creation timestamp"),
        "statecode": ("int64", "Status code: 0 for Active, 1 for Inactive"),
    },
    "SalesOrder": {
        "salesorderid": ("string", "Unique order identifier"),
        "customerid": ("string", "Foreign key reference to Contact"),
        "totalamount": ("decimal", "Total order amount in USD"),
        "createdon": ("dateTime", "ISO 8601 transaction timestamp"),
    },
}


def build_entity(name, description, columns):
    """Build one LocalEntity with its attributes and its CSV partition."""
    attributes = []
    for column in columns:
        data_type, attribute_description = ATTRIBUTE_DETAILS[name][column]
        attributes.append({"name": column, "dataType": data_type, "description": attribute_description})

    return {
        "$type": "LocalEntity",
        "name": name,
        "description": description,
        "attributes": attributes,
        "partitions": [
            {
                "name": f"{name}Partitions",
                "location": f"{name}/{name}.csv",
                "fileFormatSettings": {
                    "$type": "CsvFormatSettings",
                    "columnHeaders": True,
                    "delimiter": ",",
                },
            }
        ],
    }


def build_manifest():
    """Return the complete model.json content as a dict (the structure from the assignment).

    Lineage fields (run ID, timestamps) are added separately by extras.add_lineage().
    """
    return {
        "name": "EnterpriseDataLake",
        "description": "CDM Manifest for Ingested Customer and Sales Data",
        "version": "1.0",
        "entities": [
            build_entity("Contact", "Standard CDM Contact entity for individual customers",
                         CONTACT_COLUMNS),
            build_entity("SalesOrder", "Standard CDM SalesOrder entity for transactions",
                         SALESORDER_COLUMNS),
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


def write_manifest(path, manifest):
    """Write a manifest dict to model.json."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
