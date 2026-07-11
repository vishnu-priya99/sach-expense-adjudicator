"""Verifier Agent for checking claim details against MCP provider databases.

This module connects to the resolved provider's MCP SSE endpoint, calls the
verify_invoice tool with the claim details, and returns the response verbatim.
It guarantees a strict 5-second timeout, returning {"status": "unreachable"} on timeout.
"""

import asyncio
import json
from typing import Any, Dict
from mcp import ClientSession
from mcp.client.sse import sse_client


async def verify_claim_with_provider(
    mcp_endpoint: str, invoice_number: str, amount: float, date: str
) -> Dict[str, Any]:
    """Connects to an MCP provider over SSE to verify invoice records.

    Args:
        mcp_endpoint (str): The SSE URL endpoint of the resolved provider.
        invoice_number (str): The claim invoice number to check.
        amount (float): The claimed reimbursement amount.
        date (str): The date of the invoice (YYYY-MM-DD).

    Returns:
        dict: The provider response dictionary verbatim (e.g., {"status": "confirmed"}),
              or {"status": "unreachable"} if a 5s timeout or network issue occurs.
    """
    try:
        # Wrap everything in a strict 5.0 second timeout
        async def run_mcp_query():
            async with sse_client(mcp_endpoint) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    # 1. Initialize the session
                    await session.initialize()
                    
                    # 2. Call the verify_invoice tool
                    response = await session.call_tool(
                        "verify_invoice",
                        arguments={
                            "invoice_number": invoice_number,
                            "amount": float(amount),
                            "date": date,
                        },
                    )
                    
                    if not response or not response.content:
                        return {"status": "unreachable"}
                    
                    # 3. Parse the output content
                    res_text = response.content[0].text
                    try:
                        return json.loads(res_text)
                    except json.JSONDecodeError:
                        return {"status": "success", "raw_response": res_text}

        return await asyncio.wait_for(run_mcp_query(), timeout=5.0)

    except asyncio.TimeoutError:
        print(f"Verifier Agent: Connection to {mcp_endpoint} timed out after 5 seconds.")
        return {"status": "unreachable"}
    except Exception as e:
        print(f"Verifier Agent: Failed to verify claim via {mcp_endpoint}: {str(e)}")
        return {"status": "unreachable"}
