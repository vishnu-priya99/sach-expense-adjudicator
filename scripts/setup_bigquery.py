"""BigQuery Database Scaffolding and Seeding Script.

This script initializes the BigQuery dataset defined in `.env` and creates
the four tables needed for the sach-expense-adjudicator project:
1. provider_registry
2. policy_rules
3. claim_history
4. audit_log

It then seeds these tables with:
- 3 providers (hotel, restaurant, cab company) with endpoints from .env or defaults
- 6 realistic Indian corporate expense policy rules in INR
- 15 historical claims for employee EMP-1001 (including two client dinner claims
  from the current week at around INR 4,200 each).
"""

import datetime
import os
import sys
from google.cloud import bigquery
from google.cloud.exceptions import NotFound
from dotenv import load_dotenv

# Ensure we read environment variables
load_dotenv()


def setup_bigquery():
    project_id = os.getenv("GCP_PROJECT_ID")
    dataset_name = os.getenv("BQ_DATASET")

    if not project_id or not dataset_name:
        print("Error: GCP_PROJECT_ID or BQ_DATASET not found in .env.", file=sys.stderr)
        sys.exit(1)

    print(f"Initializing BigQuery client for project: '{project_id}'...")
    client = bigquery.Client(project=project_id)

    # 1. Create Dataset if it doesn't exist
    dataset_ref = bigquery.DatasetReference(project_id, dataset_name)
    try:
        dataset = client.get_dataset(dataset_ref)
        print(f"Dataset '{dataset_name}' already exists.")
    except NotFound:
        dataset = bigquery.Dataset(dataset_ref)
        dataset.location = "US"  # Default location
        dataset = client.create_dataset(dataset, timeout=30)
        print(f"Created dataset '{dataset_name}' in '{dataset.location}'.")

    # 2. Define Table Schemas
    schemas = {
        "provider_registry": [
            bigquery.SchemaField("gstin", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("provider_name", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("phone", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("mcp_endpoint", "STRING", mode="REQUIRED"),
        ],
        "policy_rules": [
            bigquery.SchemaField("category", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("max_amount", "FLOAT", mode="REQUIRED"),
            bigquery.SchemaField("grade", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("receipt_required_above", "FLOAT", mode="REQUIRED"),
        ],
        "claim_history": [
            bigquery.SchemaField("employee_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("invoice_no", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("vendor", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("amount", "FLOAT", mode="REQUIRED"),
            bigquery.SchemaField("claim_date", "DATE", mode="REQUIRED"),
            bigquery.SchemaField("status", "STRING", mode="REQUIRED"),
        ],
        "audit_log": [
            bigquery.SchemaField("claim_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("agent_findings", "JSON", mode="NULLABLE"),
            bigquery.SchemaField("gate_result", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("decision", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("ts", "TIMESTAMP", mode="REQUIRED"),
        ],
    }

    # 3. Create Tables
    for table_name, schema in schemas.items():
        table_id = f"{project_id}.{dataset_name}.{table_name}"
        try:
            client.get_table(table_id)
            print(f"Table '{table_name}' already exists. Re-creating/skipping...")
            # We will delete and recreate tables to ensure clean seeding
            client.delete_table(table_id, not_found_ok=True)
            print(f"Deleted existing table '{table_name}' for clean seed.")
            table = bigquery.Table(table_id, schema=schema)
            client.create_table(table, timeout=30)
            print(f"Created table '{table_name}'.")
        except NotFound:
            table = bigquery.Table(table_id, schema=schema)
            client.create_table(table, timeout=30)
            print(f"Created table '{table_name}'.")

    # 4. Load MCP endpoints from .env (with fallbacks)
    hotel_mcp = os.getenv("HOTEL_MCP_ENDPOINT", "http://localhost:8001")
    restaurant_mcp = os.getenv("RESTAURANT_MCP_ENDPOINT", "http://localhost:8002")
    cab_mcp = os.getenv("CAB_MCP_ENDPOINT", "http://localhost:8003")

    # 5. Seed Data
    print("\nSeeding data...")

    # Seeding provider_registry
    providers = [
        {
            "gstin": "27AAAAA1111A1Z1",
            "provider_name": "Taj Grand Residency Hotel",
            "phone": "+91-22-66653333",
            "mcp_endpoint": hotel_mcp,
        },
        {
            "gstin": "27BBBBB2222B2Z2",
            "provider_name": "The Olive Bistro Restaurant",
            "phone": "+91-22-26400000",
            "mcp_endpoint": restaurant_mcp,
        },
        {
            "gstin": "27CCCCC3333C3Z3",
            "provider_name": "Siddhivinayak Cabs & Travels",
            "phone": "+91-22-24300000",
            "mcp_endpoint": cab_mcp,
        },
    ]
    errors = client.insert_rows_json(f"{project_id}.{dataset_name}.provider_registry", providers)
    if errors:
        print(f"Errors seeding provider_registry: {errors}", file=sys.stderr)
    else:
        print(f"Successfully seeded {len(providers)} providers.")

    # Seeding policy_rules
    # Policy rules for an Indian corporate context (Amounts in INR)
    rules = [
        {
            "category": "Lodging",
            "max_amount": 8000.0,
            "grade": "Manager",
            "receipt_required_above": 0.0,  # Always required
        },
        {
            "category": "Lodging",
            "max_amount": 4000.0,
            "grade": "Associate",
            "receipt_required_above": 0.0,  # Always required
        },
        {
            "category": "Dining",
            "max_amount": 5000.0,
            "grade": "Manager",
            "receipt_required_above": 1000.0,
        },
        {
            "category": "Dining",
            "max_amount": 2000.0,
            "grade": "Associate",
            "receipt_required_above": 500.0,
        },
        {
            "category": "Travel",
            "max_amount": 1500.0,
            "grade": "All",
            "receipt_required_above": 300.0,
        },
        {
            "category": "Team Outing",
            "max_amount": 15000.0,
            "grade": "Director",
            "receipt_required_above": 1500.0,
        },
    ]
    errors = client.insert_rows_json(f"{project_id}.{dataset_name}.policy_rules", rules)
    if errors:
        print(f"Errors seeding policy_rules: {errors}", file=sys.stderr)
    else:
        print(f"Successfully seeded {len(rules)} policy rules.")

    # Seeding claim_history
    # Calculating dates for the 2 current-week client dinner claims
    # Let's anchor around 2026-07-11 (today in metadata)
    today = datetime.date(2026, 7, 11)
    client_dinner_1_date = (today - datetime.timedelta(days=3)).isoformat()  # July 8th, 2026
    client_dinner_2_date = (today - datetime.timedelta(days=1)).isoformat()  # July 10th, 2026

    claims = [
        # Two client dinner claims (current week, EMP-1001, around INR 4,200)
        {
            "employee_id": "EMP-1001",
            "invoice_no": "INV-2026-901",
            "vendor": "Taj Grand Residency Hotel",  # Fine dining restaurant
            "amount": 4250.0,
            "claim_date": client_dinner_1_date,
            "status": "PENDING",
        },
        {
            "employee_id": "EMP-1001",
            "invoice_no": "INV-2026-902",
            "vendor": "The Olive Bistro Restaurant",
            "amount": 4180.0,
            "claim_date": client_dinner_2_date,
            "status": "PENDING",
        },
        # 13 historical claims spread across past 3 months
        {
            "employee_id": "EMP-1001",
            "invoice_no": "INV-2026-001",
            "vendor": "Siddhivinayak Cabs & Travels",
            "amount": 450.0,
            "claim_date": "2026-06-15",
            "status": "APPROVED",
        },
        {
            "employee_id": "EMP-1001",
            "invoice_no": "INV-2026-002",
            "vendor": "Uber Cabs",
            "amount": 320.0,
            "claim_date": "2026-06-18",
            "status": "APPROVED",
        },
        {
            "employee_id": "EMP-1001",
            "invoice_no": "INV-2026-003",
            "vendor": "Hotel Ginger",
            "amount": 3800.0,
            "claim_date": "2026-06-20",
            "status": "APPROVED",
        },
        {
            "employee_id": "EMP-1001",
            "invoice_no": "INV-2026-004",
            "vendor": "Paradise Biryani House",
            "amount": 1200.0,
            "claim_date": "2026-06-21",
            "status": "APPROVED",
        },
        {
            "employee_id": "EMP-1001",
            "invoice_no": "INV-2026-005",
            "vendor": "Local Mumbai Cab",
            "amount": 2200.0,
            "claim_date": "2026-06-25",
            "status": "REJECTED",  # Exceeded cab policy of 1500 INR
        },
        {
            "employee_id": "EMP-1001",
            "invoice_no": "INV-2026-006",
            "vendor": "Starbucks Coffee",
            "amount": 450.0,
            "claim_date": "2026-06-28",
            "status": "APPROVED",
        },
        {
            "employee_id": "EMP-1001",
            "invoice_no": "INV-2026-007",
            "vendor": "Oyo Residency",
            "amount": 2500.0,
            "claim_date": "2026-05-10",
            "status": "APPROVED",
        },
        {
            "employee_id": "EMP-1001",
            "invoice_no": "INV-2026-008",
            "vendor": "Ola Cabs",
            "amount": 180.0,
            "claim_date": "2026-05-12",
            "status": "APPROVED",
        },
        {
            "employee_id": "EMP-1001",
            "invoice_no": "INV-2026-009",
            "vendor": "McDonalds",
            "amount": 650.0,
            "claim_date": "2026-05-14",
            "status": "APPROVED",
        },
        {
            "employee_id": "EMP-1001",
            "invoice_no": "INV-2026-010",
            "vendor": "Taj Mahal Palace Hotel",
            "amount": 12000.0,
            "claim_date": "2026-05-20",
            "status": "REJECTED",  # Exceeded lodging policy
        },
        {
            "employee_id": "EMP-1001",
            "invoice_no": "INV-2026-011",
            "vendor": "Uber Cabs",
            "amount": 250.0,
            "claim_date": "2026-04-05",
            "status": "APPROVED",
        },
        {
            "employee_id": "EMP-1001",
            "invoice_no": "INV-2026-012",
            "vendor": "Barbeque Nation",
            "amount": 3100.0,
            "claim_date": "2026-04-10",
            "status": "APPROVED",
        },
        {
            "employee_id": "EMP-1001",
            "invoice_no": "INV-2026-013",
            "vendor": "Dominos Pizza",
            "amount": 800.0,
            "claim_date": "2026-04-12",
            "status": "APPROVED",
        },
    ]
    errors = client.insert_rows_json(f"{project_id}.{dataset_name}.claim_history", claims)
    if errors:
        print(f"Errors seeding claim_history: {errors}", file=sys.stderr)
    else:
        print(f"Successfully seeded {len(claims)} historical claims.")

    print("\nSetup & seeding completed successfully!")


if __name__ == "__main__":
    setup_bigquery()
