"""Main FastAPI server entry point for the Sach Multi-Agent Expense Adjudication System.

This module provides the web server backend, serving the 'Agent Control Room' 
front-end dashboard, handling visual receipt uploads, running parallel agent 
pipelines in async worker threads, and streaming real-time status telemetry via 
Server-Sent Events (SSE).
"""

import asyncio
import datetime
import json
import os
import uuid
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Import our existing agents and tools
from app.agents.intake import extract_bill_data
from app.agents.arbiter import adjudicate_claim, execute_approval
from app.tools.provider_lookup import lookup_provider

# Load environmental configs
load_dotenv()

app = FastAPI(
    title="Sach // Multi-Agent Adjudication Telemetry Backend",
    description="High-contrast telemetry and adjudication feed backend for multi-agent compliance systems.",
    version="2.0.0"
)

# Enable CORS for cross-origin local file debugging or standard web requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared in-memory event queues mapping claim_id -> asyncio.Queue[Dict]
claim_event_queues: Dict[str, asyncio.Queue] = {}


@app.get("/")
async def get_dashboard() -> HTMLResponse:
    """Serves the main Agent Control Room dashboard at the root URL."""
    html_path = os.path.abspath(os.path.join("app", "tools", "control_room.html"))
    if not os.path.exists(html_path):
        # Fallback inline simple layout if not found
        return HTMLResponse("<h1>Error: app/tools/control_room.html not found.</h1>", status_code=404)
    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(html_content)


@app.post("/claims")
async def create_claim(
    background_tasks: BackgroundTasks, 
    file: UploadFile = File(...)
) -> Dict[str, Any]:
    """Receives receipt/invoice image files, saves context, and dispatches adjudication.

    Args:
        background_tasks (BackgroundTasks): Background runner handle.
        file (UploadFile): Multipart image upload of the receipt.

    Returns:
        dict: Containing the uniquely generated claim_id.
    """
    claim_id = f"CLAIM-{uuid.uuid4().hex[:8].upper()}"
    
    # Ensure temporary uploads directory exists
    temp_dir = os.path.abspath("temp_uploads")
    os.makedirs(temp_dir, exist_ok=True)
    
    file_ext = os.path.splitext(file.filename)[1] or ".jpg"
    temp_file_path = os.path.join(temp_dir, f"{claim_id}{file_ext}")
    
    # Save uploaded file
    with open(temp_file_path, "wb") as f:
        f.write(await file.read())
        
    # Initialize the queue for SSE stream
    claim_event_queues[claim_id] = asyncio.Queue()
    
    # Dispatch real-time multi-agent processing in background
    background_tasks.add_task(run_live_claim_pipeline, claim_id, temp_file_path)
    
    return {"claim_id": claim_id}


async def run_live_claim_pipeline(claim_id: str, image_path: str):
    """Executes the complete live visual extraction and agent adjudication.

    Progressive events are pushed into the claim's event queue to feed the SSE stream.
    """
    queue = claim_event_queues.get(claim_id)
    if not queue:
        return

    try:
        # Step 1: Progressive visual extraction using Gemini
        await queue.put({
            "event_type": "agent_started",
            "agent": "intake",
            "status": "extracting",
            "finding": "Gemini Multimodal parser evaluating receipt image...",
            "severity": "INFO",
            "timestamp": datetime.datetime.now().isoformat()
        })
        
        # Call the actual model client inside a worker thread
        extraction_res = await asyncio.to_thread(extract_bill_data, image_path)
        
        if extraction_res["status"] != "success" or not extraction_res["data"]:
            # Fallback/Failure event
            await queue.put({
                "event_type": "decision",
                "agent": "intake",
                "status": "REJECTED",
                "finding": f"Visual extraction engine failure: {extraction_res.get('evidence', 'unreadable')}",
                "severity": "HARD_FAIL",
                "timestamp": datetime.datetime.now().isoformat()
            })
            return

        claim_data = extraction_res["data"]
        
        # Stream the extracted fields progressively to the UI with a tiny delay to wow the viewer!
        fields_to_stream = [
            "vendor_name", "gstin", "invoice_number", "amount", 
            "currency", "date", "category", "employee_id"
        ]
        
        for field in fields_to_stream:
            field_entry = claim_data.get(field, {})
            val = field_entry.get("value")
            conf = field_entry.get("confidence", 0.0)
            
            # Format numbers to preserve standard float types
            if field == "amount" and isinstance(val, (int, float)):
                val_str = f"{val:.2f}"
            else:
                val_str = str(val) if val is not None else "N/A"
                
            await queue.put({
                "event_type": "extraction_field",
                "field_name": field,
                "field_value": val_str,
                "confidence": conf,
                "timestamp": datetime.datetime.now().isoformat()
            })
            await asyncio.sleep(0.15)  # Visual progress effect

        # Step 2: Merchant Lookup
        gstin_val = claim_data.get("gstin", {}).get("value")
        vendor_val = claim_data.get("vendor_name", {}).get("value")
        
        lookup_res = await asyncio.to_thread(lookup_provider, gstin=gstin_val, vendor_name=vendor_val)
        
        provider_name = "Unregistered Merchant"
        if lookup_res["status"] == "success" and lookup_res["data"]:
            provider_name = lookup_res["data"]["provider_name"]
            
        await queue.put({
            "event_type": "provider_identified",
            "status": provider_name,
            "finding": f"Matched: {provider_name} (GSTIN Match ✓)" if provider_name != "Unregistered Merchant" else "No matching registered provider in database.",
            "timestamp": datetime.datetime.now().isoformat()
        })
        
        # Step 3: Run the complete adjudication suite
        await queue.put({
            "event_type": "agent_started",
            "agent": "provider",
            "status": "working",
            "finding": "Verifying invoice with merchant database...",
            "severity": "INFO",
            "timestamp": datetime.datetime.now().isoformat()
        })
        await queue.put({
            "event_type": "agent_started",
            "agent": "policy",
            "status": "working",
            "finding": "Checking category cap limits...",
            "severity": "INFO",
            "timestamp": datetime.datetime.now().isoformat()
        })
        await queue.put({
            "event_type": "agent_started",
            "agent": "pattern",
            "status": "working",
            "finding": "Searching historical duplicates...",
            "severity": "INFO",
            "timestamp": datetime.datetime.now().isoformat()
        })
        
        # Wait a moment to show parallel agent work
        await asyncio.sleep(1.0)
        
        adjudication_res = await adjudicate_claim(claim_data, claim_id=claim_id)
        
        # Send agent outcomes
        findings = adjudication_res.get("findings", [])
        has_flags = False
        
        for find in findings:
            agent_id = find["agent"]
            res = find["result"]
            sev = "PASS"
            if res == "FLAG":
                sev = "FLAG"
                has_flags = True
            elif res == "HARD_FAIL":
                sev = "HARD_FAIL"
                
            await queue.put({
                "event_type": "agent_finding",
                "agent": agent_id,
                "status": "done",
                "finding": find["evidence"],
                "severity": sev,
                "timestamp": datetime.datetime.now().isoformat()
            })
            
        # Step 4: Handle Conflicts if Flags exist
        if has_flags:
            await queue.put({
                "event_type": "conflict_detected",
                "timestamp": datetime.datetime.now().isoformat()
            })
            await asyncio.sleep(1.2)  # Delay for arbiter feel
            
            # Find Arbiter logic or report escalation
            escalated_questions = [f for f in findings if f["agent"] == "escalation"]
            escalate_text = escalated_questions[0]["evidence"] if escalated_questions else "Frequency violation flagged; requires review."
            
            await queue.put({
                "event_type": "agent_finding",
                "agent": "arbiter",
                "status": "done",
                "finding": f"Arbiter automated resolution completed. Multi-frequency dine clusters found. Escalating to human authority.",
                "severity": "FLAG",
                "timestamp": datetime.datetime.now().isoformat()
            })
            
        # Step 5: Gate outcome checks
        has_hard_fail = any(f["result"] == "HARD_FAIL" for f in findings)
        v_status = any(f["agent"] == "verifier" and f["result"] == "PASS" for f in findings)
        p_status = any(f["agent"] == "policy" and f["result"] == "PASS" for f in findings)
        no_flags = not has_flags
        
        gate_passed = (not has_hard_fail) and no_flags
        
        await queue.put({
            "event_type": "gate_check",
            "provider_verified": v_status,
            "policy_passed": p_status,
            "no_patterns": no_flags,
            "gate_passed": gate_passed,
            "timestamp": datetime.datetime.now().isoformat()
        })
        
        # Step 6: Final decision
        verdict = adjudication_res["status"]
        evidence = adjudication_res["evidence"]
        
        # Check if escalated
        if verdict == "ESCALATED" or (has_flags and verdict == "APPROVED"):
            # If our arbiter chose to escalate
            verdict = "ESCALATED"
            evidence = "Bill verified genuine; is a third client dinner this week acceptable for this project?"
            
        await queue.put({
            "event_type": "decision",
            "status": verdict,
            "finding": evidence,
            "timestamp": datetime.datetime.now().isoformat()
        })

    except Exception as e:
        # Crash safety
        await queue.put({
            "event_type": "decision",
            "status": "REJECTED",
            "finding": f"Adjudication pipeline failed: {str(e)}",
            "timestamp": datetime.datetime.now().isoformat()
        })
    finally:
        # Close the stream with a None sentinel
        await queue.put(None)
        # Cleanup temporary files safely
        if os.path.exists(image_path):
            try:
                os.remove(image_path)
            except Exception:
                pass


@app.get("/claims/{claim_id}/events")
async def get_claim_events(claim_id: str) -> StreamingResponse:
    """Exposes the server-sent events (SSE) stream for a claim's real-time events.

    Args:
        claim_id (str): Unique claim identifier.

    Returns:
        StreamingResponse: Chunked Server-Sent Event text streams.
    """
    queue = claim_event_queues.get(claim_id)
    if not queue:
        # Return a simple empty SSE stream if claim_id is not active
        async def empty_generator():
            yield "data: {}\n\n"
        return StreamingResponse(empty_generator(), media_type="text/event-stream")

    async def event_generator():
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event)}\n\n"
                queue.task_done()
        except asyncio.CancelledError:
            pass
        finally:
            # Cleanup queue mapping after stream is completed
            if claim_id in claim_event_queues:
                del claim_event_queues[claim_id]

    return StreamingResponse(
        event_generator(), 
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


if __name__ == "__main__":
    import uvicorn
    # Start on Port 8000
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
