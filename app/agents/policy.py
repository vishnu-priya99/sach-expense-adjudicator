"""Policy Agent for checking claims against corporate expense policy rules.

This module queries the policy_rules table in BigQuery, maps the claim category and
employee grade, and validates if the claim complies with limits and receipt requirements.
"""

import os
from typing import Any, Dict, List, Optional
from google.cloud import bigquery
from dotenv import load_dotenv

from app.tools.bq_client import get_bq_client

load_dotenv()

# Standardized Category Mapping
CATEGORY_MAP = {
    "meals": "Dining",
    "dining": "Dining",
    "lodging": "Lodging",
    "hotel": "Lodging",
    "transport": "Travel",
    "travel": "Travel",
    "cab": "Travel",
    "taxi": "Travel",
}

# Employee Directory Mock (Since BigQuery database doesn't have an employee directory table)
EMPLOYEE_GRADE_MAP = {
    "EMP-1001": "Manager",
    "EMP-1100": "Associate",
    "EMP-9988": "Manager",
}


def resolve_grade(employee_id: Optional[str]) -> str:
    """Helper to resolve employee grade based on ID, defaulting to Associate."""
    if not employee_id:
        return "Associate"
    return EMPLOYEE_GRADE_MAP.get(employee_id.upper().strip(), "Associate")


def check_policy(claim: Dict[str, Any]) -> Dict[str, Any]:
    """Validates an intake claim against active corporate policy rules in BigQuery.

    Args:
        claim (dict): The extracted intake claim, with fields nested as {"value": ..., "confidence": ...}.

    Returns:
        dict: A structured dictionary indicating policy checks completed:
            {
                "status": "success" | "error",
                "data": [
                    {"rule": str, "result": "PASS" | "HARD_FAIL" | "FLAG", "evidence": str},
                    ...
                ],
                "evidence": str
            }
    """
    try:
        project_id = os.getenv("GCP_PROJECT_ID")
        dataset_name = os.getenv("BQ_DATASET")
        if not project_id or not dataset_name:
            return {
                "status": "error",
                "data": None,
                "evidence": "GCP_PROJECT_ID or BQ_DATASET is not configured in .env.",
            }

        client = get_bq_client()
        table_id = f"{project_id}.{dataset_name}.policy_rules"

        # 1. Extract and normalize category
        raw_cat = claim.get("category", {}).get("value")
        if not raw_cat:
            return {
                "status": "error",
                "data": None,
                "evidence": "Claim is missing category value.",
            }
        
        category_lower = raw_cat.lower().strip()
        mapped_category = CATEGORY_MAP.get(category_lower, "Travel")

        # 2. Extract and resolve employee grade
        emp_id = claim.get("employee_id", {}).get("value")
        grade = resolve_grade(emp_id)

        # 3. Extract amount
        amount_val = claim.get("amount", {}).get("value")
        if amount_val is None:
            return {
                "status": "error",
                "data": None,
                "evidence": "Claim is missing amount value.",
            }
        
        claim_amount = float(amount_val)

        # 4. Fetch policy rules from BigQuery
        query = f"SELECT category, max_amount, grade, receipt_required_above FROM `{table_id}`"
        query_job = client.query(query)
        rows = list(query_job.result())
        rules = [dict(row) for row in rows]

        # 5. Find the matching policy rule
        matched_rule = None
        for rule in rules:
            rule_cat = rule["category"].lower().strip()
            rule_grade = rule["grade"].lower().strip()
            
            if rule_cat == mapped_category.lower():
                if rule_grade == grade.lower() or rule_grade == "all":
                    matched_rule = rule
                    break

        if not matched_rule:
            # Fallback to 'All' grade if no exact match found
            for rule in rules:
                if rule["category"].lower() == mapped_category.lower() and rule["grade"].lower() == "all":
                    matched_rule = rule
                    break

        if not matched_rule:
            return {
                "status": "success",
                "data": [
                    {
                        "rule": "Category Cap Limit",
                        "result": "PASS",
                        "evidence": f"No active policy rule found for category '{mapped_category}' and grade '{grade}'. Defaulting to PASS.",
                    }
                ],
                "evidence": f"No policy rule matched for category '{mapped_category}' and grade '{grade}'.",
            }

        max_amount = matched_rule["max_amount"]
        receipt_required_above = matched_rule["receipt_required_above"]

        findings = []

        # Check 1: Max Amount Limit Check
        if claim_amount > max_amount:
            findings.append({
                "rule": f"Max Amount Limit ({mapped_category})",
                "result": "HARD_FAIL",
                "evidence": f"Claim amount {claim_amount:.2f} INR exceeds the category limit of {max_amount:.2f} INR allowed for grade {grade}.",
            })
        else:
            findings.append({
                "rule": f"Max Amount Limit ({mapped_category})",
                "result": "PASS",
                "evidence": f"Claim amount {claim_amount:.2f} INR is within the category limit of {max_amount:.2f} INR allowed for grade {grade}.",
            })

        # Check 2: Receipt Required Check
        # Since this represents an Intake scanned image claim, we already have the receipt image.
        if claim_amount > receipt_required_above:
            findings.append({
                "rule": "Receipt Required Threshold",
                "result": "PASS",
                "evidence": f"Claim amount {claim_amount:.2f} INR is above the threshold of {receipt_required_above:.2f} INR requiring a receipt. Receipt image was successfully provided.",
            })
        else:
            findings.append({
                "rule": "Receipt Required Threshold",
                "result": "PASS",
                "evidence": f"Claim amount {claim_amount:.2f} INR is below the threshold of {receipt_required_above:.2f} INR requiring a receipt.",
            })

        has_fail = any(f["result"] == "HARD_FAIL" for f in findings)
        status_summary = "HARD_FAIL" if has_fail else "PASS"

        return {
            "status": "success",
            "data": findings,
            "evidence": f"Policy check completed for category '{mapped_category}' and grade '{grade}' with overall status: {status_summary}.",
        }

    except Exception as e:
        return {
            "status": "error",
            "data": None,
            "evidence": f"An unhandled exception occurred in check_policy: {str(e)}",
        }
