"""The async wallet the MCP tools sit on top of.

EVERY BLOCKING CALL IN THIS PACKAGE GOES THROUGH `asyncio.to_thread`, AND THAT IS
NOT A STYLE RULE. P0.1 measured a ~900 ms event-loop stall from one algod round
trip. An MCP server that blocks its loop stops answering *every* request, not
just the paying one - so a single forgotten `await`-less algod call would make
the whole server appear to hang whenever an agent pays for something.

Three responsibilities beyond wrapping the CLI's logic:

**Reap on startup.** Whatever the last run left behind is swept before the first
tool answers. This is where an unclean exit stops costing money.

**Open a session lazily.** Not at startup - a server that opens a funded account
the moment a client connects charges an agent that may never pay for anything.
The first paid call opens one; everything after reuses it.

**Close an idle session.** MCP has no reliable session-end signal on any
transport, so a balance would otherwise sit in a session account until the next
process start. An idle timeout is the honest mitigation: it does not solve the
problem, it bounds it.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from .. import algorand
from ..caps import SpendContext
from ..errors import WalletError
from ..config import VAULT_MIN_ALGO_MICRO, Config
from ..keys import Key
from ..ledger import Ledger, SessionRecord
from ..session import (
    close_session,
    live_session,
    open_session,
    open_vault,
    reap,
    vault_status,
)
from ..x402 import PaymentResult, fetch


@dataclass
class SessionHandle:
    key: Key
    record: SessionRecord
    ledger: Ledger


class Wallet:
    """One vault, at most one session, driven from an event loop."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        # Serialises session open/close. Two concurrent tool calls that both find
        # no session would otherwise both open one, and v1 runs one session per
        # vault (DESIGN.md decision 6) - the second would fail after the first had
        # already moved money.
        self._lock = asyncio.Lock()
        self._last_used = time.monotonic()
        self._closing = False

    # ---- lifecycle -------------------------------------------------------

    async def startup(self) -> list[str]:
        """Sweep anything a previous run left behind. Returns human-readable lines."""
        results = await asyncio.to_thread(reap, self.cfg)
        return [f"session {r.index} {r.address}: {status}" for r, _txid, status in results]

    async def shutdown(self) -> None:
        """Close the open session, if any. Best effort - the reaper is the backstop."""
        async with self._lock:
            try:
                await asyncio.to_thread(close_session, self.cfg)
            except WalletError:
                pass  # nothing open; not an error

    async def idle_monitor(self, interval: float = 30.0) -> None:
        """Close a session that has gone unused. Runs for the server's lifetime."""
        while True:
            await asyncio.sleep(interval)
            idle = time.monotonic() - self._last_used
            if idle < self.cfg.idle_timeout_seconds:
                continue
            async with self._lock:
                try:
                    await asyncio.to_thread(close_session, self.cfg)
                except WalletError:
                    pass  # no live session; nothing to do
                self._last_used = time.monotonic()

    # ---- session ---------------------------------------------------------

    async def _current(self) -> SessionHandle | None:
        try:
            key, record, ledger = await asyncio.to_thread(live_session, self.cfg)
        except WalletError:
            return None
        return SessionHandle(key=key, record=record, ledger=ledger)

    async def ensure_session(self) -> SessionHandle:
        """Return the open session, opening one on first use.

        The lock is held across the whole open so two concurrent first calls
        cannot both fund an account.
        """
        async with self._lock:
            if handle := await self._current():
                return handle
            await asyncio.to_thread(
                open_session, self.cfg, self.cfg.caps.session_balance_micro
            )
            handle = await self._current()
            if handle is None:  # opened but not readable - refuse to guess
                raise RuntimeError("Opened a session but could not read it back.")
            return handle

    # ---- tool bodies -----------------------------------------------------

    async def status(self) -> dict[str, Any]:
        st = await asyncio.to_thread(vault_status, self.cfg)
        handle = await self._current()
        fmt = self.cfg.network.fmt

        session: dict[str, Any] | None = None
        if handle is not None:
            remaining = max(0, handle.record.balance_micro - handle.record.spent_micro)
            session = {
                "address": handle.record.address,
                "balance": fmt(handle.record.balance_micro),
                "spent": fmt(handle.record.spent_micro),
                "remaining": fmt(remaining),
                "idle_seconds": round(time.monotonic() - self._last_used),
            }

        ledger = await asyncio.to_thread(Ledger.load, self.cfg.ledger_path)
        return {
            "network": self.cfg.network.name,
            "payment_asset": self.cfg.network.payment_asa,
            "vault": {
                "address": st.address,
                "algo": f"{st.algo_micro / 1e6:.6f}",
                "asset": fmt(st.asset_micro),
                "opted_in": st.opted_in,
                "ready": st.ready,
            },
            "session": session,
            "spent_today": fmt(ledger.spent_today()),
            "caps": {
                "per_call": fmt(self.cfg.caps.per_call_micro),
                "daily": fmt(self.cfg.caps.daily_micro)
                if self.cfg.caps.daily_micro is not None
                else None,
                "session_balance": fmt(self.cfg.caps.session_balance_micro),
                "allowlist_entries": len(self.cfg.caps.allowlist),
            },
        }

    async def funding_info(self) -> dict[str, Any]:
        st = await asyncio.to_thread(vault_status, self.cfg)
        asset = self.cfg.network.payment_asa
        steps = [
            {
                "step": 1,
                "done": st.algo_micro >= VAULT_MIN_ALGO_MICRO,
                "who": "human",
                "action": (
                    f"Send at least {VAULT_MIN_ALGO_MICRO / 1e6:.2f} ALGO to "
                    f"{st.address}."
                ),
                "why": (
                    "The vault cannot pay the fee for its own asset opt-in without "
                    "it, and 0.1 ALGO is locked as the asset slot minimum."
                ),
            },
            {
                "step": 2,
                "done": st.opted_in,
                "who": "obol",
                "action": f"Opt the vault into ASA {asset} (`obol vault optin`).",
                "why": (
                    "Until this is done, USDC sent to the vault is REJECTED outright "
                    "- it does not sit pending, it fails."
                ),
            },
            {
                "step": 3,
                "done": st.asset_micro > 0,
                "who": "human",
                "action": f"Send USDC (ASA {asset}) to {st.address}.",
                "why": "With the opt-in done, the transfer will arrive.",
            },
        ]
        return {
            "network": self.cfg.network.name,
            "vault_address": st.address,
            "payment_asset": asset,
            "ready": st.ready,
            "current_step": st.step,
            "next_action": st.message,
            "steps": steps,
            "note": (
                "These three steps are in a forced order and cannot be reordered. "
                "An onramp delivers USDC and not ALGO, so step 1 still needs doing "
                "even when a card is used."
            ),
        }

    async def fetch(
        self,
        url: str,
        method: str = "GET",
        body: str | None = None,
        max_price_micro: int | None = None,
    ) -> dict[str, Any]:
        vault, _ = await asyncio.to_thread(open_vault, self.cfg)
        self._last_used = time.monotonic()

        # Read the ledger WITHOUT opening a session. An unpaid URL, a price over
        # the cap, or an unlisted merchant must all cost nothing - and opening a
        # session funds a real account, so it happens only when a payment is
        # certain. `fetch` calls the provider below at that instant and not before.
        existing = await self._current()
        ledger = existing.ledger if existing else await asyncio.to_thread(
            Ledger.load, self.cfg.ledger_path
        )
        remaining = (
            max(0, existing.record.balance_micro - existing.record.spent_micro)
            if existing
            # No session yet: the cap to check against is what one would be
            # funded with, since that is what the payment would spend from.
            else self.cfg.caps.session_balance_micro
        )
        spend = SpendContext(
            spent_today_micro=ledger.spent_today(),
            session_remaining_micro=remaining,
        )

        opened: list[SessionHandle] = []

        async def provider() -> Key:
            handle = await self.ensure_session()
            opened.append(handle)
            return handle.key

        result: PaymentResult = await fetch(
            self.cfg,
            provider,
            url,
            method=method,
            body=body.encode() if body is not None else None,
            max_price_micro=max_price_micro,
            our_addresses={vault.address},
            spend=spend,
        )

        if result.paid:
            handle = opened[0] if opened else existing
            if handle is not None:
                await asyncio.to_thread(
                    handle.ledger.record_spend, handle.record, result.price_micro
                )
        self._last_used = time.monotonic()

        payload: dict[str, Any] = {
            "url": result.url,
            "status": result.status_code,
            "paid": result.paid,
            "content_type": result.content_type,
            "body": result.content.decode("utf-8", "replace"),
        }
        if result.paid:
            payload |= {
                "price": self.cfg.network.fmt(result.price_micro),
                "pay_to": result.pay_to,
                "payer": result.payer,
                "txid": result.txid,
                "settled": result.receipt.get("success"),
            }
        return payload


async def probe_vault_ready(cfg: Config) -> bool:
    """Cheap readiness check that does not open a session."""
    st = await asyncio.to_thread(vault_status, cfg)
    return st.ready


__all__ = ["Wallet", "SessionHandle", "probe_vault_ready", "algorand"]
