"""BigQuery Client Helper and Agent Audit Logging Tool.

This module provides helpers to interface with Google Cloud BigQuery and implements
the tool used to log all agent decisions to the `audit_log` table as required by the
workspace rules. It handles dynamic configuration from environment variables, robust
error catching, and auto-creation of datasets/tables.
"""

import datetime
import json
import os
import uuid
from typing import Any, Dict, Optional
from google.cloud import bigquery
from google.cloud.exceptions import NotFound
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()


def get_bq_client() -> bigquery.Client:
    """Initializes and returns a BigQuery client using project ID from environment.

    Returns:
        bigquery.Client: The initialized BigQuery client instance.

    Raises:
        ValueError: If GCP_PROJECT_ID is not configured in .env.
    """
    project_id = os.getenv("GCP_PROJECT_ID")
    if not project_id:
        raise ValueError("GCP_PROJECT_ID is not configured in the .env file.")
    return bigquery.Client(project=project_id)


def ensure_audit_log_table_exists(client: bigquery.Client) -> str:
    """Ensures that the BQ dataset and audit_log table exist.

    Args:
        client (bigquery.Client): The BigQuery client instance.

    Returns:
        str: The fully qualified table ID string.

    Raises:
        ValueError: If BQ_DATASET is not configured in .env.
    """
    project_id = os.getenv("GCP_PROJECT_ID")
    dataset_name = os.getenv("BQ_DATASET")

    if not project_id or not dataset_name:
        raise ValueError("GCP_PROJECT_ID or BQ_DATASET is not configured in .env.")

    dataset_ref = bigquery.DatasetReference(project_id, dataset_name)
    table_id = f"{project_id}.{dataset_name}.audit_log"

    # 1. Ensure Dataset Exists
    try:
        client.get_dataset(dataset_ref)
    except NotFound:
        dataset = bigquery.Dataset(dataset_ref)
        dataset.location = "US"  # Default location
        client.create_dataset(dataset, timeout=30)

    # 2. Ensure Table Exists
    schema = [
        bigquery.SchemaField("claim_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("agent_findings", "JSON", mode="NULLABLE"),
        bigquery.SchemaField("gate_result", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("decision", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("ts", "TIMESTAMP", mode="REQUIRED"),
    ]

    try:
        client.get_table(table_id)
    except NotFound:
        table = bigquery.Table(table_id, schema=schema)
        client.create_table(table, timeout=30)

    return table_id


def audit_decision(
    claim_id: str,
    agent_findings: Dict[str, Any],
    gate_result: str,
    decision: str,
) -> Dict[str, Any]:
    """Logs an agent decision to the BigQuery audit_log table.

    As per the workspace rules, this tool function never raises unhandled exceptions
    and returns a structured dict of the form:
    {"status": "success" | "error", "data": ..., "evidence": ...}

    Args:
        claim_id (str): The ID of the expense claim being audited.
        agent_findings (dict): Evaluated findings from the agent.
        gate_result (str): The result status of safety/security gate checks.
        decision (str): The final decision made (e.g. APPROVED, REJECTED, ESCALATED).

    Returns:
        dict: A structured dictionary indicating the operation's outcome and evidence.
    """
    try:
        # Validate inputs
        if not claim_id or not decision:
            return {
                "status": "error",
                "data": None,
                "evidence": "claim_id and decision are required fields.",
            }

        client = get_bq_client()
        table_id = ensure_audit_log_table_exists(client)

        # Prepare decision row data
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

        row_to_insert = {
            "claim_id": claim_id,
            "agent_findings": json.dumps(agent_findings) if isinstance(agent_findings, dict) else agent_findings,
            "gate_result": gate_result,
            "decision": decision,
            "ts": timestamp,
        }

        # Insert row into BigQuery
        errors = client.insert_rows_json(table_id, [row_to_insert])

        if errors:
            return {
                "status": "error",
                "data": {"errors": errors},
                "evidence": f"BigQuery row insertion encountered errors: {errors}",
            }

        return {
            "status": "success",
            "data": {
                "claim_id": claim_id,
                "ts": timestamp,
                "table_id": table_id,
            },
            "evidence": f"Decision for claim {claim_id} successfully logged to BigQuery table {table_id}.",
        }

    except Exception as e:
        return {
            "status": "error",
            "data": None,
            "evidence": f"An unhandled exception occurred in audit_decision: {str(e)}",
        }
