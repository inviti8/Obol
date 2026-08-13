"""The three MCP tools. One of them is the product.

TOOL DESCRIPTIONS ARE PROMPTS, AND THESE ONES CARRY CAVEATS ON PURPOSE.
An agent reads the description and nothing else before deciding to spend money,
so anything it needs to know about what a payment does and does not prove has to
be in the text. DESIGN.md section 7 is explicit that this is where honesty lives:
a wallet that oversells what a receipt means is worse than one that says nothing.

No `authen_notarize`. Authen is reached through `x402_fetch` like any other
resource - the moment Obol grows first-class verbs for one merchant it stops
being a wallet and becomes that merchant's SDK.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..errors import CapExceeded, PaymentRefused, PaymentRejected, WalletError
from .wallet import Wallet

X402_FETCH_DESCRIPTION = """\
Fetch a URL, paying automatically if it answers with an x402 payment challenge.

SPENDS REAL MONEY when the network is mainnet. Payment comes from a short-lived
session account funded from the user's vault, never from the vault itself, so the
most any single call can lose is the session balance.

Returns the response body plus, when a payment happened, the price, the merchant
address, the transaction id and the settlement receipt.

What the receipt proves: that this payment settled on chain. It does NOT prove
the resource was correct, honest, or worth the price - a settled payment and a
useful answer are different claims.

Refuses, without spending, when: the price exceeds max_price_usdc or the
configured per-call cap; the day's spending cap would be exceeded; the merchant
is outside an allowlist the user enabled; the resource asks for an asset this
wallet does not hold; the payTo address is one of ours (paying yourself is not a
payment); or a mainnet resource is not https. Each refusal says which rule it hit.

Unpaid URLs are fine - if the server answers normally, the body is returned and
nothing is spent."""

WALLET_STATUS_DESCRIPTION = """\
Report the wallet's state: network, vault address and balances, the active
session and what remains in it, spending so far today, and the configured caps.

Read this before assuming a payment can be made. A vault that is not yet funded
or not yet opted into the payment asset cannot pay for anything, and this is
where that shows up. Costs nothing and spends nothing."""

WALLET_FUNDING_INFO_DESCRIPTION = """\
Explain how to put money in this wallet, and report which of the three setup
steps the vault is on.

For the human, not the agent - it returns an address and instructions, and no
part of it can be automated by the agent.

The three steps are in a forced order: ALGO must arrive before the vault can opt
into the payment asset, and the opt-in must happen before USDC can be received at
all. USDC sent to a vault that has not opted in is rejected outright - it does
not sit pending, it fails."""


def register(server: Any, wallet: Wallet) -> None:
    """Attach the tools to an MCPServer instance."""

    @server.tool(name="x402_fetch", description=X402_FETCH_DESCRIPTION)
    async def x402_fetch(
        url: str,
        method: str = "GET",
        body: str | None = None,
        max_price_usdc: float | None = None,
    ) -> dict[str, Any]:
        max_price = (
            wallet.cfg.network.to_units(max_price_usdc)
            if max_price_usdc is not None
            else None
        )
        try:
            return await wallet.fetch(
                url, method=method, body=body, max_price_micro=max_price
            )
        except CapExceeded as exc:
            # Named separately so the agent can tell "raise your limit" apart from
            # "this is not allowed" - one is a setting, the other never will be.
            return {
                "error": "cap_exceeded",
                "limit": exc.limit,
                "message": str(exc),
                "spent": False,
            }
        except PaymentRefused as exc:
            return {"error": "refused", "message": str(exc), "spent": False}
        except WalletError as exc:
            # Not funded, not opted in, no session available. The agent cannot fix
            # any of these itself - point it at the tool that explains them.
            return {
                "error": "wallet_not_ready",
                "message": str(exc),
                "spent": False,
                "hint": "Call wallet_funding_info for the setup steps.",
            }
        except httpx.RequestError as exc:
            # The resource was unreachable, so nothing was challenged, validated or
            # signed. Worth its own branch because it is by far the most common
            # failure in practice and reads as alarming when it surfaces raw.
            return {
                "error": "unreachable",
                "message": f"Could not reach {url}: {exc.__class__.__name__}: {exc}",
                "spent": False,
            }
        except PaymentRejected as exc:
            # We signed and sent. The money may have moved even though no resource
            # came back, so this must never read as a safe-to-retry failure.
            return {
                "error": "rejected",
                "message": str(exc),
                "spent": "possibly",
                "note": (
                    "A payment was signed and sent but the resource was not served. "
                    "Check wallet_status before retrying - retrying blindly can pay "
                    "twice for something you did not get."
                ),
            }

    @server.tool(name="wallet_status", description=WALLET_STATUS_DESCRIPTION)
    async def wallet_status() -> dict[str, Any]:
        try:
            return await wallet.status()
        except WalletError as exc:
            return {"error": "wallet_not_ready", "message": str(exc)}

    @server.tool(
        name="wallet_funding_info", description=WALLET_FUNDING_INFO_DESCRIPTION
    )
    async def wallet_funding_info() -> dict[str, Any]:
        try:
            return await wallet.funding_info()
        except WalletError as exc:
            return {"error": "wallet_not_ready", "message": str(exc)}
