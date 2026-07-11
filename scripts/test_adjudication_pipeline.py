"""End-to-End Adjudication Pipeline Verification Suite.

This script runs all visual specimen receipts in `demo_bills/` through the complete
multi-stage expense claim adjudication pipeline:
1. Intake Agent (Multimodal extraction & injection resilience)
2. Arbiter Agent (Parallel Provider Verification, Policy Cap, and Behavioral Patterns)
3. Final Decision Outcome (Approval, Rejection, or Escalation)
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

# Ensure app directory is on path for clean imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.agents.intake import extract_bill_data
from app.agents.arbiter import adjudicate_claim

load_dotenv()


async def run_pipeline():
    demo_bills_dir = r"c:\Users\vishn\OneDrive\Desktop\CV_Hackathon\demo_bills"
    if not os.path.exists(demo_bills_dir):
        print(f"Error: demo_bills directory not found at {demo_bills_dir}")
        return

    test_files = [f for f in os.listdir(demo_bills_dir) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
    test_files.sort()

    print("======================================================================")
    print("STARTING E2E EXPENSE ADJUDICATION PIPELINE RUN")
    print("======================================================================")
    print(f"Discovered {len(test_files)} test specimens in demo_bills/\n")

    summary_results = []

    for filename in test_files:
        image_path = os.path.join(demo_bills_dir, filename)
        print("\n" + "=" * 80)
        print(f"PROCESSING SPECIMEN: {filename}")
        print("=" * 80)

        # Step 1: Multimodal Intake Extraction
        print(f"[Stage 1] Invoking Multimodal Intake Agent for {filename}...")
        intake_res = extract_bill_data(image_path)
        
        status = intake_res.get("status")
        evidence = intake_res.get("evidence")
        claim_data = intake_res.get("data")

        if status == "invalid_image":
            print(f"[Stage 1 Result] Handled Non-Bill specimen gracefully. Status: {status}")
            print(f"Evidence: {evidence}")
            summary_results.append({
                "file": filename,
                "intake_status": "REJECTED (Not a Bill)",
                "adjudication_decision": "REJECTED_NOT_A_BILL",
                "reason": "Gracefully identified non-bill image specimen.",
            })
            continue
        elif status == "error":
            print(f"[Stage 1 Result] Error extracting claim data: {evidence}")
            summary_results.append({
                "file": filename,
                "intake_status": "ERROR",
                "adjudication_decision": "FAILED",
                "reason": f"Intake error: {evidence}",
            })
            continue

        print(f"[Stage 1 Result] Success! Extracted data: {claim_data}")

        # Step 2: Adjudication Layer (Arbiter)
        print(f"[Stage 2] Invoking Adjudication Arbiter Agent on claim data...")
        claim_id = f"CLAIM-{filename.split('.')[0].upper()}"
        adjudicate_res = await adjudicate_claim(claim_data, claim_id=claim_id)

        decision = adjudicate_res.get("status")
        final_evidence = adjudicate_res.get("evidence")
        findings = adjudicate_res.get("findings", [])

        print(f"\n[Stage 2 Result] Adjudication Complete! Decision: {decision}")
        print(f"Evidence: {final_evidence}")
        print("Detailed Agent Findings:")
        for idx, finding in enumerate(findings):
            print(f"  - [{finding['agent'].upper()}] {finding['rule_or_pattern']}: {finding['result']} | {finding['evidence']}")

        if decision == "ESCALATED":
            pkg = adjudicate_res.get("escalation_package", {})
            print(f"\n--- ESCALATION PACKAGE GENERATED FOR HUMAN REVIEW ---")
            print(f"Summary: {pkg.get('summary')}")
            print(f"Disagreement: {pkg.get('disagreement')}")
            print(f"Question for Human: {pkg.get('one_question_for_human')}")
            print(f"------------------------------------------------------")

        summary_results.append({
            "file": filename,
            "intake_status": "EXTRACTED",
            "adjudication_decision": decision,
            "reason": final_evidence,
        })

    # Print Final Summary Report
    print("\n" + "=" * 80)
    print("E2E PIPELINE EXECUTION SUMMARY")
    print("=" * 80)
    print(f"{'FILE':<25} | {'INTAKE STATUS':<22} | {'ADJUDICATION':<15} | {'REASON SUMMARY'}")
    print("-" * 110)
    for res in summary_results:
        print(f"{res['file']:<25} | {res['intake_status']:<22} | {res['adjudication_decision']:<15} | {res['reason'][:50]}...")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_pipeline())
