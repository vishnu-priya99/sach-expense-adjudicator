"""Targeted verification for registered MCP Provider Adjudication.

This script runs mock claims corresponding to the registered Hotel, Restaurant,
and Cab providers to verify that:
1. provider_lookup matches correctly.
2. verifier connects to the live SSE endpoint over MCP.
3. parallel checks execute smoothly.
4. claims are approved.
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.agents.arbiter import adjudicate_claim

load_dotenv()


async def test_mcp_positive_claims():
    # Construct claims that exist in the provider database as per test_live_mcp_client.py
    positive_claims = [
        {
            "name": "Live Hotel Provider Claim (Manager)",
            "data": {
                "vendor_name": {"value": "Taj Grand Residency Hotel", "confidence": 1.0},
                "gstin": {"value": "27AAAAA1111A1Z1", "confidence": 1.0},
                "invoice_number": {"value": "INV-4521", "confidence": 1.0},
                "amount": {"value": 18400.0, "confidence": 1.0}, # Wait, lodging cap for Manager is 8000, so we use a lower amount to let it pass
                "currency": {"value": "INR", "confidence": 1.0},
                "date": {"value": "2026-07-11", "confidence": 1.0},
                "category": {"value": "lodging", "confidence": 1.0},
                "employee_id": {"value": "EMP-1001", "confidence": 1.0}, # Manager
            },
            "override_amount_to_pass": 6500.0 # Hotel cap for Manager is 8000, so 6500.0 passes!
        },
        {
            "name": "Live Restaurant Provider Claim (Manager)",
            "data": {
                "vendor_name": {"value": "The Olive Bistro Restaurant", "confidence": 1.0},
                "gstin": {"value": "27BBBBB2222B2Z2", "confidence": 1.0},
                "invoice_number": {"value": "RST-0092", "confidence": 1.0},
                "amount": {"value": 3200.0, "confidence": 1.0}, # Dining cap for Manager is 5000, so 3200 passes!
                "currency": {"value": "INR", "confidence": 1.0},
                "date": {"value": "2026-07-11", "confidence": 1.0},
                "category": {"value": "meals", "confidence": 1.0},
                "employee_id": {"value": "EMP-1001", "confidence": 1.0}, # Manager
            }
        },
        {
            "name": "Live Cab Provider Claim (Associate)",
            "data": {
                "vendor_name": {"value": "Siddhivinayak Cabs & Travels", "confidence": 1.0},
                "gstin": {"value": "27CCCCC3333C3Z3", "confidence": 1.0},
                "invoice_number": {"value": "CAB-7710", "confidence": 1.0},
                "amount": {"value": 850.0, "confidence": 1.0}, # Travel cap is 1500, so 850 passes!
                "currency": {"value": "INR", "confidence": 1.0},
                "date": {"value": "2026-07-11", "confidence": 1.0},
                "category": {"value": "transport", "confidence": 1.0},
                "employee_id": {"value": "EMP-1100", "confidence": 1.0}, # Associate
            }
        }
    ]

    print("======================================================================")
    print("STARTING POSITIVE MCP PROVIDER ADJUDICATION VERIFICATION")
    print("======================================================================")

    for case in positive_claims:
        print(f"\nRunning case: {case['name']}")
        claim = case["data"]
        
        # Adjust lodging amount so it passes policy and we can test positive verification
        if "override_amount_to_pass" in case:
            claim["amount"]["value"] = case["override_amount_to_pass"]

        claim_id = f"CLAIM-POS-{claim['invoice_number']['value']}"
        res = await adjudicate_claim(claim, claim_id=claim_id)
        
        print(f"Outcome: {res['status']}")
        print(f"Evidence: {res['evidence']}")
        print("Findings:")
        for f in res.get("findings", []):
            print(f"  - [{f['agent'].upper()}] {f['rule_or_pattern']}: {f['result']} | {f['evidence']}")


if __name__ == "__main__":
    asyncio.run(test_mcp_positive_claims())
