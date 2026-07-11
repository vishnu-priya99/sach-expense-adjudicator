"""Test script for verifying the Intake Agent.

This script runs the multimodal Intake Agent on each image in the `demo_bills/`
directory, prints the extracted structured JSON to the console, and verifies
successful parsing.
"""

import os
import json
from app.agents.intake import extract_bill_data

def main():
    print("==================================================")
    print("Starting E2E Verification of Multimodal Intake Agent")
    print("==================================================")
    
    demo_bills_dir = "demo_bills"
    if not os.path.exists(demo_bills_dir):
        print(f"Error: Directory '{demo_bills_dir}' does not exist.")
        return

    bill_files = [f for f in os.listdir(demo_bills_dir) if f.lower().endswith(('.jpg', '.png', '.jpeg'))]
    if not bill_files:
        print(f"No image files found in '{demo_bills_dir}' directory.")
        return

    print(f"Found {len(bill_files)} bill image(s) to process:")
    for f in bill_files:
        print(f" - {f}")
    print("--------------------------------------------------\n")

    for f in bill_files:
        image_path = os.path.join(demo_bills_dir, f)
        print(f">>> Processing: {f} ...")
        
        result = extract_bill_data(image_path)
        
        print(f"Status: {result.get('status').upper()}")
        print(f"Evidence: {result.get('evidence')}")
        
        if result.get("status") == "success":
            print("Extracted Structured JSON:")
            print(json.dumps(result.get("data"), indent=2))
        elif result.get("status") == "invalid_image":
            print("Gracefully Handled Non-Bill Image (All fields empty):")
            print(json.dumps(result.get("data"), indent=2))
        else:
            print(f"Extraction failed! Reason: {result.get('evidence')}")
            
        print("-" * 50 + "\n")


    print("==================================================")
    print("Intake Agent Verification Complete!")
    print("==================================================")

if __name__ == "__main__":
    main()
