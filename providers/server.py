"""Reusable MCP Invoice Provider Server.

This module implements a reusable Model Context Protocol (MCP) server that exposes a
`verify_invoice` tool. It runs over HTTP Server-Sent Events (SSE), making it ready for
deployment to Google Cloud Run. Invoices are loaded into memory at startup from a
JSON database path configured via the `INVOICES_FILE_PATH` environment variable.
"""

import json
import os
import sys
from typing import Any, Dict
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount
import uvicorn

# Initialize the FastMCP server instance with DNS rebinding protection disabled for Cloud Run external routing
from mcp.server.transport_security import TransportSecuritySettings

mcp = FastMCP(
    "invoice-provider",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )
)

print("CONTAINER STARTUP: transport_security =", mcp.settings.transport_security, flush=True)

# Global in-memory dictionary to store loaded invoices
INVOICES: Dict[str, Dict[str, Any]] = {}


def load_invoices() -> None:
    """Loads invoices from the JSON database configured via environment variable."""
    global INVOICES
    file_path = os.getenv("INVOICES_FILE_PATH")

    if not file_path:
        print(
            "Warning: INVOICES_FILE_PATH environment variable not set. Starting with empty database.",
            file=sys.stderr,
        )
        INVOICES = {}
        return

    if not os.path.exists(file_path):
        print(
            f"Error: Invoices file '{file_path}' does not exist. Starting with empty database.",
            file=sys.stderr,
        )
        INVOICES = {}
        return

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            INVOICES = json.load(f)
        print(
            f"Successfully loaded {len(INVOICES)} invoices from '{file_path}' into memory."
        )
    except Exception as e:
        print(
            f"Error: Failed to parse invoices JSON from '{file_path}': {str(e)}",
            file=sys.stderr,
        )
        INVOICES = {}


@mcp.tool()
def verify_invoice(invoice_number: str, amount: float, date: str) -> Dict[str, Any]:
    """Verifies whether an invoice is valid, has the correct amount, and matches records.

    Args:
        invoice_number (str): The unique identifier of the invoice to verify.
        amount (float): The claimed reimbursement amount.
        date (str): The date the invoice was issued (YYYY-MM-DD).

    Returns:
        dict: A dictionary containing exactly one of:
            - {"status": "confirmed"}
            - {"status": "no_such_invoice"}
            - {"status": "amount_mismatch", "recorded_amount": <float>}
    """
    # 1. Look up the invoice in our in-memory database
    # Handle potential casing mismatches gracefully
    record = INVOICES.get(invoice_number) or INVOICES.get(invoice_number.upper())

    if not record:
        return {"status": "no_such_invoice"}

    # 2. Extract recorded values
    recorded_amount = float(record.get("amount", 0.0))

    # 3. Compare amount (allowing a small float precision tolerance)
    if abs(recorded_amount - float(amount)) > 0.01:
        return {"status": "amount_mismatch", "recorded_amount": recorded_amount}

    # If both invoice is found and amount matches
    return {"status": "confirmed"}


# Load invoices into memory at startup
load_invoices()

# Mount the MCP SSE endpoint to Starlette
# FastMCP provides an out-of-the-box sse_app() ASGI sub-application
app = Starlette(
    routes=[
        Mount("/", app=mcp.sse_app()),
    ]
)

if __name__ == "__main__":
    # Cloud Run injects the PORT environment variable
    port = int(os.getenv("PORT", "8080"))
    print(f"Starting MCP Invoice Provider Server on port {port} over SSE...")
    uvicorn.run(app, host="0.0.0.0", port=port)
