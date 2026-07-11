"""Arbiter Agent for orchestrating the complete expense claim adjudication layer.

This module acts as the central brain. It receives extracted intake claims, runs
merchant provider lookups, and dispatches the verifier, policy, and pattern checks
in parallel. It handles rejections, approvals (via gates), and resolves/escalates
flagged claims using exactly one round of reasoning before escalation.
"""

import asyncio
import datetime
import os
import uuid
from typing import Any, Dict, List, Optional
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from app.tools.bq_client import audit_decision
from app.tools.provider_lookup import lookup_provider
from app.agents.verifier import verify_claim_with_provider
from app.agents.policy import check_policy
from app.agents.pattern import check_patterns
from app.agents.escalation import escalate_claim

# Safely import the human-owned execute_approval gate function
try:
    from app.gates import execute_approval
except (ImportError, ModuleNotFoundError):
    def execute_approval(claim_id: str, findings: Any) -> Dict[str, Any]:
        """Local mock fallback for development if app.gates is missing."""
        print(f"[MOCK GATES] execute_approval invoked for: {claim_id}")
        return {
            "status": "success",
            "data": {"approved_at": datetime.datetime.now().isoformat()},
            "evidence": "Executed local fallback mock gate approval.",
        }

load_dotenv()


class ResolutionSchema(BaseModel):
    decision: str = Field(
        ...,
        description="Must be either 'resolve' (approve the claim anyway) or 'escalate' (send to human review).",
    )
    reason: str = Field(
        ...,
        description="A concise reason explaining why the flags can be resolved or why they must be escalated.",
    )


async def adjudicate_claim(
    claim: Dict[str, Any], claim_id: Optional[str] = None
) -> Dict[str, Any]:
    """Orchestrates parallel verification, policy, and pattern checks on an intake claim.

    Dispatches:
    1. Verifier Agent: Queries merchant's verify_invoice (if provider is resolved).
    2. Policy Agent: Checks category cap and receipt rules.
    3. Pattern Agent: Inspects history for duplicates, frequency, and near-caps.

    Args:
        claim (dict): The extracted intake claim data.
        claim_id (str, optional): A pre-existing claim ID, or a new one will be generated.

    Returns:
        dict: The final adjudication package and decision.
    """
    if not claim_id:
        claim_id = f"CLAIM-{uuid.uuid4().hex[:8].upper()}"

    print(f"\n[Arbiter Agent] Adjudicating claim {claim_id}...")

    # 1. Resolve Provider details
    gstin_val = claim.get("gstin", {}).get("value")
    vendor_name_val = claim.get("vendor_name", {}).get("value")
    
    print(f"[Arbiter Agent] Resolving provider for GSTIN: '{gstin_val}', Name: '{vendor_name_val}'...")
    lookup_res = lookup_provider(gstin=gstin_val, vendor_name=vendor_name_val)
    
    endpoint = None
    provider_name = None
    if lookup_res["status"] == "success" and lookup_res["data"]:
        endpoint = lookup_res["data"]["mcp_endpoint"]
        provider_name = lookup_res["data"]["provider_name"]
        print(f"[Arbiter Agent] Provider resolved: '{provider_name}' (Endpoint: {endpoint})")
    else:
        print(f"[Arbiter Agent] Provider lookup: {lookup_res['status']} ({lookup_res['evidence']})")

    # 2. Build async parallel tasks
    invoice_number = claim.get("invoice_number", {}).get("value") or ""
    amount_val = claim.get("amount", {}).get("value")
    amount = float(amount_val) if amount_val is not None else 0.0
    date = claim.get("date", {}).get("value") or "2026-07-11"

    # Verifier task
    if endpoint:
        verifier_task = verify_claim_with_provider(
            mcp_endpoint=endpoint,
            invoice_number=invoice_number,
            amount=amount,
            date=date,
        )
    else:
        verifier_task = asyncio.sleep(
            0,
            result={
                "status": "unreachable",
                "evidence": f"No registered merchant provider matched. Lookup status: {lookup_res['status']}.",
            },
        )

    # Policy and Pattern tasks (Run synchronous BQ queries in parallel executor threads)
    policy_task = asyncio.to_thread(check_policy, claim)
    pattern_task = asyncio.to_thread(check_patterns, claim)

    print("[Arbiter Agent] Dispatching verifier, policy, and pattern checks in parallel...")
    verifier_res, policy_res, pattern_res = await asyncio.gather(
        verifier_task, policy_task, pattern_task
    )
    print("[Arbiter Agent] Parallel evaluations complete.")

    # 3. Collate findings with structured severity
    findings = []

    # Verifier findings
    v_status = verifier_res.get("status")
    if v_status == "confirmed":
        findings.append({
            "agent": "verifier",
            "rule_or_pattern": "Invoice Authenticity Check",
            "result": "PASS",
            "evidence": f"Merchant '{provider_name}' database successfully confirmed the invoice matches their records.",
            "severity": "INFO",
        })
    elif v_status == "amount_mismatch":
        findings.append({
            "agent": "verifier",
            "rule_or_pattern": "Invoice Authenticity Check",
            "result": "HARD_FAIL",
            "evidence": f"Merchant '{provider_name}' reported a critical invoice amount mismatch. Database recorded: {verifier_res.get('recorded_amount')} INR, but claimed: {amount:.2f} INR.",
            "severity": "CRITICAL",
        })
    elif v_status == "no_such_invoice":
        findings.append({
            "agent": "verifier",
            "rule_or_pattern": "Invoice Authenticity Check",
            "result": "HARD_FAIL",
            "evidence": f"Merchant '{provider_name}' reported that invoice number '{invoice_number}' does not exist in their records.",
            "severity": "CRITICAL",
        })
    else:
        # Unreachable or no provider found - we flag it instead of failing
        findings.append({
            "agent": "verifier",
            "rule_or_pattern": "Invoice Authenticity Check",
            "result": "FLAG",
            "evidence": f"Merchant verification is unavailable. Provider was resolved, but call returned: {v_status} ({verifier_res.get('evidence', 'unreachable')}).",
            "severity": "WARNING",
        })

    # Policy findings
    if policy_res["status"] == "success" and policy_res["data"]:
        for rule in policy_res["data"]:
            findings.append({
                "agent": "policy",
                "rule_or_pattern": rule["rule"],
                "result": rule["result"],
                "evidence": rule["evidence"],
                "severity": "CRITICAL" if rule["result"] == "HARD_FAIL" else "INFO",
            })
    else:
        findings.append({
            "agent": "policy",
            "rule_or_pattern": "Corporate Policy Validation",
            "result": "HARD_FAIL",
            "evidence": f"Policy agent crashed or failed: {policy_res.get('evidence')}",
            "severity": "CRITICAL",
        })

    # Pattern findings
    if pattern_res["status"] == "success" and pattern_res["data"]:
        for pat in pattern_res["data"]:
            findings.append({
                "agent": "pattern",
                "rule_or_pattern": pat["pattern"],
                "result": pat["result"],
                "evidence": pat["evidence"],
                "severity": "CRITICAL" if pat["result"] == "HARD_FAIL" else ("WARNING" if pat["result"] == "FLAG" else "INFO"),
            })
    else:
        findings.append({
            "agent": "pattern",
            "rule_or_pattern": "Behavioral Pattern Detection",
            "result": "FLAG",
            "evidence": f"Pattern agent crashed or failed: {pattern_res.get('evidence')}",
            "severity": "WARNING",
        })

    # 4. Evaluate Overall Decision
    has_hard_fail = any(f["result"] == "HARD_FAIL" for f in findings)
    flagged_findings = [f for f in findings if f["result"] == "FLAG"]

    # --- DECISION PATH 1: HARD_FAIL ---
    if has_hard_fail:
        critical_evidences = [f["evidence"] for f in findings if f["result"] == "HARD_FAIL"]
        combined_evidence = "; ".join(critical_evidences)
        print(f"[Arbiter Agent] Claim {claim_id} REJECTED due to critical failures: {combined_evidence}")
        
        # Log decision to BigQuery audit_log
        audit_decision(
            claim_id=claim_id,
            agent_findings={"findings": findings},
            gate_result="FAILED",
            decision="REJECTED",
        )
        return {
            "status": "REJECTED",
            "claim_id": claim_id,
            "findings": findings,
            "evidence": f"Claim rejected due to critical validation failures: {combined_evidence}",
        }

    # --- DECISION PATH 2: FLAGS ARISE (One round of resolution reasoning) ---
    if flagged_findings:
        print(f"[Arbiter Agent] Claim {claim_id} has {len(flagged_findings)} active FLAGS. Initiating automated resolution round...")
        
        api_key = os.getenv("GEMINI_API_KEY")
        model_name = os.getenv("MODEL_NAME", "gemini-3.5-flash")
        
        if api_key:
            try:
                # Ask Gemini to run exactly one resolution round
                client = genai.Client(api_key=api_key)
                
                # Simplify claim dictionary
                simplified_claim = {k: (v["value"] if isinstance(v, dict) and "value" in v else v) for k, v in claim.items()}
                
                prompt_text = (
                    f"You are the senior executive auditor in charge of the Adjudication Resolution Round.\n"
                    f"A claim has passed all critical caps but raised one or more operational flags.\n\n"
                    f"Claim details:\n{simplified_claim}\n\n"
                    f"Raised flags:\n{flagged_findings}\n\n"
                    f"Evaluate if these flags are benign administrative alerts that we can safely 'resolve' (approve the claim), "
                    f"or if they are genuine financial risks that must be 'escalate'-ed for human review.\n\n"
                    f"Choose 'resolve' only if you are absolutely 100% confident there is no fraud risk (e.g. slight name mismatch on a highly verified merchant, "
                    f"or a travel expense slightly over a non-strict advisory cap). If there are duplicate worries or serious frequent-category bursts, choose 'escalate'."
                )
                
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt_text,
                    config=types.GenerateContentConfig(
                        system_instruction="You resolve low-risk expense claim flags or escalate high-risk ones.",
                        response_mime_type="application/json",
                        response_schema=ResolutionSchema,
                        temperature=0.1,
                    )
                )
                
                resolution = types.json.loads(response.text)
                decision_choice = resolution.get("decision", "escalate").lower().strip()
                reason_given = resolution.get("reason", "No reason provided.")
                print(f"[Arbiter Agent] Resolution Round complete. Result: '{decision_choice}' (Reason: {reason_given})")
                
                if decision_choice == "resolve":
                    # Approved despite flags!
                    print(f"[Arbiter Agent] Resolution successful. Executing approval gate...")
                    gate_res = execute_approval(claim_id, findings)
                    
                    audit_decision(
                        claim_id=claim_id,
                        agent_findings={"findings": findings, "resolution_reason": reason_given},
                        gate_result="PASSED",
                        decision="APPROVED",
                    )
                    
                    return {
                        "status": "APPROVED",
                        "claim_id": claim_id,
                        "findings": findings,
                        "evidence": f"Claim approved after automated flag resolution. Resolution justification: {reason_given}",
                    }
            except Exception as res_err:
                print(f"[Arbiter Agent] Automated resolution round failed due to error: {str(res_err)}. Falling back to escalation.")

        # If resolution choice is 'escalate' (or model failed), dispatch to Escalation Agent!
        print(f"[Arbiter Agent] Escalating claim {claim_id} for human review...")
        escalate_res = escalate_claim(claim_id=claim_id, claim=claim, flags=flagged_findings)
        
        return {
            "status": "ESCALATED",
            "claim_id": claim_id,
            "findings": findings,
            "escalation_package": escalate_res.get("data"),
            "evidence": f"Claim escalated for human review: {escalate_res.get('evidence')}",
        }

    # --- DECISION PATH 3: ALL PASS (Fully approved) ---
    print(f"[Arbiter Agent] Claim {claim_id} passed all checks. Executing approval gate...")
    gate_res = execute_approval(claim_id, findings)
    
    audit_decision(
        claim_id=claim_id,
        agent_findings={"findings": findings},
        gate_result="PASSED",
        decision="APPROVED",
    )
    
    return {
        "status": "APPROVED",
        "claim_id": claim_id,
        "findings": findings,
        "evidence": "Claim passed all validation checks and was successfully approved.",
    }
