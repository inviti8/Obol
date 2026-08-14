"""Drive the Obol MCP server as a real client, over a real stdio transport.

    uv run python probes/mcp_client.py

Phase 4's done-condition is "an agent completes a paid notarization without the
human touching a key". This is that, minus the model: a genuine MCP client
spawns `obolus-mcp` as a subprocess, negotiates the protocol, lists the tools, and
calls them. Nothing here reaches into `obol` internals - if a tool is misdeclared
or a return value will not serialise, this fails exactly where a real client
would.

Requires the Authen node on 127.0.0.1:8402 (probes/_authen_node.py) and a funded
vault in OBOL_DATA_DIR.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

NOTARIZE = "http://127.0.0.1:8402/api/v1/notarize"


def _summarise(result) -> dict:
    """Pull the structured payload out of a CallToolResult."""
    if getattr(result, "structured_content", None):
        return result.structured_content
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return {}


async def main() -> int:
    if os.environ.get("OBOL_NETWORK") == "mainnet":
        raise SystemExit("This probe pays. Testnet only.")

    # Spawn the server exactly as an MCP client would: a command, over stdio.
    # A bare string is treated as an HTTP URL by the SDK, not a command line -
    # StdioServerParameters is what selects the subprocess transport.
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "obolus.mcp.server"],
        env=dict(os.environ),  # carries OBOL_DATA_DIR / OBOL_NETWORK through
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    async with Client(stdio_client(params)) as client:
        info = client.server_info
        print(f"connected  {info.name} {info.version}")
        print(f"protocol   {client.protocol_version}")
        print()

        tools = await client.list_tools()
        print(f"tools ({len(tools.tools)}):")
        for tool in tools.tools:
            first_line = (tool.description or "").strip().splitlines()[0]
            print(f"  {tool.name:22} {first_line[:60]}")
        print()

        status = _summarise(await client.call_tool("wallet_status", {}))
        print("wallet_status:")
        print(f"  network  {status.get('network')} asset {status.get('payment_asset')}")
        print(f"  vault    {status.get('vault', {}).get('address')}")
        print(f"  algo     {status.get('vault', {}).get('algo')}")
        print(f"  asset    {status.get('vault', {}).get('asset')}")
        print(f"  ready    {status.get('vault', {}).get('ready')}")
        print(f"  session  {status.get('session')}")
        print()

        funding = _summarise(await client.call_tool("wallet_funding_info", {}))
        print(f"wallet_funding_info: step {funding.get('current_step')} - "
              f"{funding.get('next_action')}")
        print()

        # The refusal path first: it must cost nothing.
        refused = _summarise(
            await client.call_tool(
                "x402_fetch",
                {
                    "url": NOTARIZE,
                    "method": "POST",
                    "body": "cap probe",
                    "max_price_usdc": 0.001,
                },
            )
        )
        print("x402_fetch (max_price 0.001, must refuse):")
        print(f"  error {refused.get('error')} limit={refused.get('limit')} "
              f"spent={refused.get('spent')}")
        print()

        # The real thing.
        paid = _summarise(
            await client.call_tool(
                "x402_fetch",
                {
                    "url": NOTARIZE,
                    "method": "POST",
                    "body": "Obol Phase 4: an agent paid for this over MCP",
                },
            )
        )
        print("x402_fetch (paid):")
        print(f"  status   {paid.get('status')}  paid={paid.get('paid')}")
        print(f"  price    {paid.get('price')} to {paid.get('pay_to')}")
        print(f"  payer    {paid.get('payer')}")
        print(f"  txid     {paid.get('txid')}  settled={paid.get('settled')}")
        body = paid.get("body") or ""
        print(f"  body     {len(body)} bytes")
        try:
            attestation = json.loads(body).get("attestation", "")
            print(f"  attest   {attestation[:48]}...")
        except Exception:
            pass
        print()

        after = _summarise(await client.call_tool("wallet_status", {}))
        print("wallet_status after:")
        print(f"  session  {after.get('session')}")
        print(f"  today    {after.get('spent_today')}")

        ok = paid.get("paid") and paid.get("settled") and refused.get("error") == "cap_exceeded"
        print()
        print("VERDICT:", "agent paid over MCP, no key touched" if ok else "FAILED")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.exit(asyncio.run(main()))
