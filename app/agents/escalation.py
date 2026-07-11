"""Escalation Agent for handling flagged claims that require human review.

This module reviews flagged claims, analyzes the flags, and generates a structured
escalation package consisting of a summary, a summary of disagreement, and one clear
question for a human auditor to resolve the claim. It then logs the escalation to BigQuery.
"""

import os
from typing import Any, Dict, List
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from app.tools.bq_client import audit_decision

load_dotenv()


class EscalationSchema(BaseModel):
    summary: str = Field(
        ...,
        description="A concise summary of the claim, the matched provider, and what flags were raised.",
    )
    disagreement: str = Field(
        ...,
        description="The conflict or disagreement (e.g. 'The invoice was confirmed by the merchant, but it violates the high frequency pattern rule').",
    )
    one_question_for_human: str = Field(
        ...,
        description="Exactly one, direct multiple-choice or yes/no question for a human auditor to resolve this claim.",
    )


def escalate_claim(
    claim_id: str, claim: Dict[str, Any], flags: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Analyzes a flagged claim and generates a structured escalation package for humans.

    Args:
        claim_id (str): The unique ID of the claim.
        claim (dict): The claim details.
        flags (list): List of flagged anomalies or pattern findings.

    Returns:
        dict: A structured dictionary indicating escalation package generated:
            {
                "status": "success" | "error",
                "data": {
                    "summary": str,
                    "disagreement": str,
                    "one_question_for_human": str
                },
                "evidence": str
            }
    """
    try:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return {
                "status": "error",
                "data": None,
                "evidence": "GEMINI_API_KEY is not configured in .env.",
            }

        model_name = os.getenv("MODEL_NAME", "gemini-3.5-flash")
        client = genai.Client(api_key=api_key)

        # Simplify claim dictionary for the prompt (nests value and confidence)
        simplified_claim = {}
        for key, field in claim.items():
            if isinstance(field, dict) and "value" in field:
                simplified_claim[key] = field["value"]
            else:
                simplified_claim[key] = field

        prompt_text = (
            f"Review the following expense claim details and the flags raised by our system:\n\n"
            f"--- CLAIM DETAILS ---\n{simplified_claim}\n\n"
            f"--- FLAGS RAISED ---\n{flags}\n\n"
            f"Formulate a structured escalation package containing a clear, professional summary of the claim, "
            f"the core points of conflict or risk (disagreement), and exactly one actionable question "
            f"for a human auditor to decide whether to approve or reject the claim."
        )

        system_instruction = (
            "You are a senior financial risk auditor. Your job is to summarize flagged expense anomalies "
            "and raise them to human managers with maximum clarity and zero fluff."
        )

        # Generate structured output using Gemini
        response = client.models.generate_content(
            model=model_name,
            contents=prompt_text,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=EscalationSchema,
                temperature=0.1,
            ),
        )

        res_text = response.text
        if not res_text:
            raise ValueError("Empty response from Gemini Escalation model.")

        escalation_data = json_data = types.json.loads(res_text)

        # Log the escalation decision to BigQuery audit_log
        audit_res = audit_decision(
            claim_id=claim_id,
            agent_findings=escalation_data,
            gate_result="WARNING",
            decision="ESCALATED",
        )

        evidence_msg = "Successfully generated escalation package."
        if audit_res.get("status") == "success":
            evidence_msg += f" Logged to BigQuery audit_log under claim_id: {claim_id}."
        else:
            evidence_msg += f" Warning: BQ audit log failed: {audit_res.get('evidence')}."

        return {
            "status": "success",
            "data": escalation_data,
            "evidence": evidence_msg,
        }

    except Exception as e:
        return {
            "status": "error",
            "data": None,
            "evidence": f"An unhandled exception occurred in escalate_claim: {str(e)}",
        }
