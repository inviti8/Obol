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
from ..files import read_body, resolve_within, write_output
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
from ..view import ViewCard, open_in_browser, render_page, write_page
from ..x402 import PaymentResult, fetch


# Enough for a JSON attestation or a page of text; short of anything that would
# swamp an agent's context. Bigger bodies go to output_file.
INLINE_BODY_LIMIT = 64_000


def _matches_asset(target, wanted: str, label: str) -> bool:
    """Match how a human would say it: 'algo', 'usdc', or the raw asset id."""
    w = wanted.strip().lower()
    return w in {target.what.lower(), target.theme.key, target.theme.label.lower()} or (
        w in {"asset", "token", label.lower()} and target.what != "ALGO"
    )


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
            "payment_asset_label": self.cfg.network.asset_label,
            "balances": {
                "ALGO": f"{st.algo_micro / 1e6:.6f}",
                self.cfg.network.asset_label: fmt(st.asset_micro),
            },
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

    async def funding_info(
        self, asset: str | None = None, qr_dir: str | None = None
    ) -> dict[str, Any]:
        from ..funding import default_logo, funding_targets, qr_styled_png

        st = await asyncio.to_thread(vault_status, self.cfg)
        asset_id = self.cfg.network.payment_asa
        label = self.cfg.network.asset_label
        targets = funding_targets(
            st.address,
            asset_id,
            network=self.cfg.network.name,
            algo_needed_micro=VAULT_MIN_ALGO_MICRO,
            asset_label=label,
        )
        by_key = {t.theme.key: t for t in targets}
        THEME_KEY_ASSET = next(k for k in by_key if k != "algo")
        if asset is not None:
            targets = [t for t in targets if _matches_asset(t, asset, label)]
            if not targets:
                raise WalletError(
                    f"Unknown asset {asset!r}. This wallet holds ALGO and {label}."
                )
        # Steps are always built from the FULL set, then filtered. Building them
        # from the filtered list indexed positionally is how "top up USDC" started
        # reading the ALGO entry.
        wanted = {t.theme.key for t in targets}
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
                "scan": by_key["algo"].uri,
                "asset": "ALGO",
            },
            {
                "step": 2,
                "done": st.opted_in,
                "who": "obolus",
                "action": f"Opt the vault into ASA {asset_id} (`obol vault optin`).",
                "why": (
                    "Until this is done, USDC sent to the vault is REJECTED outright "
                    "- it does not sit pending, it fails."
                ),
            },
            {
                "step": 3,
                "done": st.asset_micro > 0,
                "who": "human",
                "action": f"Send {label} (ASA {asset_id}) to {st.address}.",
                "why": "With the opt-in done, the transfer will arrive.",
                "scan": by_key[THEME_KEY_ASSET].uri,
                "asset": label,
            },
        ]
        info: dict[str, Any] = {
            "network": self.cfg.network.name,
            "vault_address": st.address,
            "payment_asset": asset_id,
            "payment_asset_label": label,
            "ready": st.ready,
            "current_step": st.step,
            "next_action": st.message,
            "steps": [
                s
                for s in steps
                # Step 2 is Obol's own opt-in and is a precondition for the asset
                # transfer, so it stays whenever the asset side is in scope.
                if asset is None
                or (s["step"] == 1 and "algo" in wanted)
                or (s["step"] in (2, 3) and THEME_KEY_ASSET in wanted)
            ],
            "asked_about": label if asset and THEME_KEY_ASSET in wanted else (
                "ALGO" if asset else "both"
            ),
            "balances": {
                "ALGO": f"{st.algo_micro / 1e6:.6f}",
                label: self.cfg.network.fmt(st.asset_micro),
            },
            "note": (
                "These three steps are in a forced order and cannot be reordered. "
                "An onramp delivers USDC and not ALGO, so step 1 still needs doing "
                "even when a card is used."
            ),
            "scan_note": (
                "`scan` values are ARC-26 URIs a wallet can scan. They carry the "
                "address and the asset id but deliberately NO amount - the human "
                "types that into their own wallet, where they see it before "
                "confirming. Run `obol vault qr` for the same thing as a QR code "
                "in a terminal."
            ),
        }
        if qr_dir is not None:
            written = []
            for target in targets:
                name = target.theme.key
                rel = f"{qr_dir.rstrip('/')}/obol-fund-{self.cfg.network.name}-{name}.png"
                path = await asyncio.to_thread(
                    write_output,
                    self.cfg.file_root,
                    rel,
                    qr_styled_png(
                        target.uri,
                        logo=default_logo(),
                        dark=target.theme.modules,
                        light=target.theme.background,
                        caption=target.theme.label,
                    ),
                )
                written.append(str(path))
            info["qr_written"] = written
        return info

    async def funding_view(self, asset: str | None = None) -> dict[str, Any]:
        """Render the funding codes as a page and open it in a browser.

        The MCP image block is correct and some clients render it; a terminal
        client does not, which leaves the one person who needs to point a phone
        at the code unable to see it. This is the fallback that always works.
        """
        from ..funding import default_logo, funding_targets, qr_styled_png

        st = await asyncio.to_thread(vault_status, self.cfg)
        label = self.cfg.network.asset_label
        targets = funding_targets(
            st.address,
            self.cfg.network.payment_asa,
            network=self.cfg.network.name,
            algo_needed_micro=VAULT_MIN_ALGO_MICRO,
            asset_label=label,
        )
        if asset is not None:
            targets = [t for t in targets if _matches_asset(t, asset, label)]
            if not targets:
                raise WalletError(
                    f"Unknown asset {asset!r}. This wallet holds ALGO and {label}."
                )

        cards = [
            ViewCard(
                label=t.theme.label,
                uri=t.uri,
                png=qr_styled_png(
                    t.uri,
                    logo=default_logo(),
                    dark=t.theme.modules,
                    light=t.theme.background,
                    caption=t.theme.label,
                ),
                why=t.why,
                background=t.theme.background,
                suggested=t.suggested,
            )
            for t in targets
        ]
        page = render_page(
            cards,
            network=self.cfg.network.name,
            address=st.address,
            balances={
                "ALGO": f"{st.algo_micro / 1e6:.6f}",
                label: self.cfg.network.fmt(st.asset_micro),
            },
        )
        tag = targets[0].theme.key if asset is not None else "all"
        path = await asyncio.to_thread(
            write_page, page, network=self.cfg.network.name, tag=tag
        )
        opened = await asyncio.to_thread(open_in_browser, path)
        return {
            "page": str(path),
            "opened_in_browser": opened,
            "showing": [c.label for c in cards],
        }

    async def funding_qr(self, asset: str) -> tuple[str, str, bytes]:
        """One asset's funding code as PNG bytes. Returns (label, uri, png)."""
        from ..funding import default_logo, funding_targets, qr_styled_png

        st = await asyncio.to_thread(vault_status, self.cfg)
        label = self.cfg.network.asset_label
        targets = funding_targets(
            st.address,
            self.cfg.network.payment_asa,
            network=self.cfg.network.name,
            algo_needed_micro=VAULT_MIN_ALGO_MICRO,
            asset_label=label,
        )
        picked = next((t for t in targets if _matches_asset(t, asset, label)), None)
        if picked is None:
            raise WalletError(
                f"Unknown asset {asset!r}. This wallet holds ALGO and {label}."
            )
        png = qr_styled_png(
            picked.uri,
            logo=default_logo(),
            dark=picked.theme.modules,
            light=picked.theme.background,
            caption=picked.theme.label,
        )
        return picked.theme.label, picked.uri, png

    async def fetch(
        self,
        url: str,
        method: str = "GET",
        body: str | None = None,
        max_price_micro: int | None = None,
        body_file: str | None = None,
        output_file: str | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        if body_file is not None and body is not None:
            raise WalletError("Pass body or body_file, not both.")
        # Read BEFORE anything else: a path that escapes the root must refuse
        # without touching the network or the wallet.
        payload: bytes | None = None
        if body_file is not None:
            payload = await asyncio.to_thread(read_body, self.cfg.file_root, body_file)
        elif body is not None:
            payload = body.encode()
        # An output path is validated up front too, so a doomed write is not
        # discovered after a payment has already settled.
        if output_file is not None:
            resolve_within(self.cfg.file_root, output_file, purpose="output_file")

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
            body=payload,
            headers={"Content-Type": content_type} if content_type else None,
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

        out: dict[str, Any] = {
            "url": result.url,
            "status": result.status_code,
            "paid": result.paid,
            "content_type": result.content_type,
            "bytes": len(result.content),
        }
        if result.headers:
            out["response_headers"] = result.headers

        if output_file is not None:
            written = await asyncio.to_thread(
                write_output, self.cfg.file_root, output_file, result.content
            )
            out["written_to"] = str(written)
        else:
            # Never hand back replacement characters. A C2PA-signed image is not
            # text, and decoding it with errors="replace" produces a body that
            # looks like data and is not - which is worse than saying so.
            try:
                text = result.content.decode("utf-8")
            except UnicodeDecodeError:
                out["body_encoding"] = "binary"
                out["note"] = (
                    "The response is binary and was not returned inline. Re-run "
                    "with output_file to write it to disk."
                )
            else:
                if len(text) > INLINE_BODY_LIMIT:
                    out["body"] = text[:INLINE_BODY_LIMIT]
                    out["truncated"] = True
                    out["note"] = (
                        f"Body truncated at {INLINE_BODY_LIMIT} characters. Use "
                        "output_file to get all of it."
                    )
                else:
                    out["body"] = text
        if result.paid:
            out |= {
                "price": self.cfg.network.fmt(result.price_micro),
                "pay_to": result.pay_to,
                "payer": result.payer,
                "txid": result.txid,
                "settled": result.receipt.get("success"),
            }
        return out


async def probe_vault_ready(cfg: Config) -> bool:
    """Cheap readiness check that does not open a session."""
    st = await asyncio.to_thread(vault_status, cfg)
    return st.ready


__all__ = ["Wallet", "SessionHandle", "probe_vault_ready", "algorand"]
