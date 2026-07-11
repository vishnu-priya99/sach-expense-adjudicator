"""Provider Lookup Tool for Resolving Expense Claim Merchants.

This module provides tools to lookup and resolve invoice merchant entities from
the BigQuery provider_registry table based on extracted claim attributes (GSTIN,
phone, or merchant name). It matches by GSTIN exact, fallback to phone, and fallback
to fuzzy name.
"""

import difflib
import os
import re
from typing import Any, Dict, Optional
from google.cloud import bigquery
from dotenv import load_dotenv

from app.tools.bq_client import get_bq_client

load_dotenv()


def clean_string(s: Optional[str]) -> str:
    """Helper to clean and normalize a string for comparison."""
    if not s:
        return ""
    # Strip non-alphanumeric and convert to lowercase
    return re.sub(r"[^a-zA-Z0-9]", "", s).lower()


def get_live_endpoint_override(provider_name: str, registry_endpoint: str) -> str:
    """Overloads registry localhost endpoints with live Cloud Run endpoints from .env."""
    prov_lower = provider_name.lower()
    if "hotel" in prov_lower or "taj" in prov_lower:
        endpoint = os.getenv("HOTEL_MCP_ENDPOINT")
        if endpoint:
            return endpoint
    elif "restaurant" in prov_lower or "olive" in prov_lower or "grill" in prov_lower:
        endpoint = os.getenv("RESTAURANT_MCP_ENDPOINT")
        if endpoint:
            return endpoint
    elif "cab" in prov_lower or "travel" in prov_lower or "siddhivinayak" in prov_lower:
        endpoint = os.getenv("CAB_MCP_ENDPOINT")
        if endpoint:
            return endpoint
    return registry_endpoint


def lookup_provider(
    gstin: Optional[str] = None,
    phone: Optional[str] = None,
    vendor_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolves an expense claim merchant from the provider_registry table in BigQuery.

    Matches by:
    1. Exact GSTIN match.
    2. Fallback to exact phone match.
    3. Fallback to fuzzy vendor_name match.

    Returns a structured dictionary matching code standards:
        {
            "status": "success" | "ambiguous" | "error",
            "data": <Resolved Provider Dict or None>,
            "evidence": <Detailed explanation of the resolution process>
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
        table_id = f"{project_id}.{dataset_name}.provider_registry"

        # Fetch all registered providers to run our matching logic in memory (since the registry is small)
        query = f"SELECT gstin, provider_name, phone, mcp_endpoint FROM `{table_id}`"
        query_job = client.query(query)
        rows = list(query_job.result())

        providers = [dict(row) for row in rows]
        if not providers:
            return {
                "status": "error",
                "data": None,
                "evidence": f"No providers registered in {table_id}.",
            }

        # 1. Exact GSTIN Match Check
        if gstin:
            cleaned_target_gstin = clean_string(gstin)
            if cleaned_target_gstin:
                for prov in providers:
                    if clean_string(prov.get("gstin")) == cleaned_target_gstin:
                        resolved_prov = prov.copy()
                        resolved_prov["mcp_endpoint"] = get_live_endpoint_override(
                            prov["provider_name"], prov["mcp_endpoint"]
                        )
                        return {
                            "status": "success",
                            "data": resolved_prov,
                            "evidence": f"Resolved provider exactly by GSTIN match: '{gstin}'.",
                        }

        # 2. Fallback Phone Match Check
        if phone:
            cleaned_target_phone = clean_string(phone)
            if cleaned_target_phone:
                for prov in providers:
                    if clean_string(prov.get("phone")) == cleaned_target_phone:
                        resolved_prov = prov.copy()
                        resolved_prov["mcp_endpoint"] = get_live_endpoint_override(
                            prov["provider_name"], prov["mcp_endpoint"]
                        )
                        return {
                            "status": "success",
                            "data": resolved_prov,
                            "evidence": f"Resolved provider exactly by Phone match: '{phone}'.",
                        }

        # 3. Fallback Fuzzy Vendor Name Match
        if vendor_name:
            normalized_vendor = vendor_name.lower().strip()
            
            # Simple substring/word-overlap check
            exact_or_substring_matches = []
            for prov in providers:
                prov_name_lower = prov["provider_name"].lower().strip()
                if normalized_vendor in prov_name_lower or prov_name_lower in normalized_vendor:
                    exact_or_substring_matches.append(prov)

            if len(exact_or_substring_matches) == 1:
                resolved_prov = exact_or_substring_matches[0].copy()
                resolved_prov["mcp_endpoint"] = get_live_endpoint_override(
                    resolved_prov["provider_name"], resolved_prov["mcp_endpoint"]
                )
                return {
                    "status": "success",
                    "data": resolved_prov,
                    "evidence": f"Resolved provider by substring match: '{vendor_name}' matches '{resolved_prov['provider_name']}'.",
                }
            
            # If no unique substring match, run difflib sequence matching
            best_prov = None
            best_score = 0.0
            candidate_matches = []

            for prov in providers:
                score = difflib.SequenceMatcher(
                    None, normalized_vendor, prov["provider_name"].lower().strip()
                ).ratio()
                if score >= 0.55:  # High-confidence threshold
                    candidate_matches.append((prov, score))
                    if score > best_score:
                        best_score = score
                        best_prov = prov

            # Filter candidates matching the highest score
            best_candidates = [c for c in candidate_matches if abs(c[1] - best_score) < 0.01]

            if len(best_candidates) == 1 and best_score >= 0.55:
                resolved_prov = best_prov.copy()
                resolved_prov["mcp_endpoint"] = get_live_endpoint_override(
                    resolved_prov["provider_name"], resolved_prov["mcp_endpoint"]
                )
                return {
                    "status": "success",
                    "data": resolved_prov,
                    "evidence": f"Resolved provider by fuzzy name match (similarity: {best_score:.2f}): '{vendor_name}' matches '{resolved_prov['provider_name']}'.",
                }
            elif len(best_candidates) > 1:
                names = [c[0]["provider_name"] for c in best_candidates]
                return {
                    "status": "ambiguous",
                    "data": None,
                    "evidence": f"Ambiguous vendor name lookup: '{vendor_name}' matched multiple registered providers: {names}.",
                }

        return {
            "status": "ambiguous",
            "data": None,
            "evidence": f"No confident match found in provider registry for GSTIN: '{gstin}', Phone: '{phone}', Name: '{vendor_name}'.",
        }

    except Exception as e:
        return {
            "status": "error",
            "data": None,
            "evidence": f"An unhandled exception occurred in lookup_provider: {str(e)}",
        }
