"""Spend controls. Every one of these is the user's to set, raise or turn off.

WHAT ACTUALLY BOUNDS A LOSS IS NOT IN THIS FILE. The session balance does that,
on chain, because an account cannot spend what it does not hold. Nothing here
survives a compromised process - a caller that skips these functions simply is
not capped. They are defence in depth, and they protect the user's own money,
which is why they earn their place regardless of who funded the wallet.

Kept separate from the refusals in `x402.guard` for a reason that shows up in the
error message: a cap can be raised by editing config.toml, and a refusal cannot.
Telling an agent "raise your daily limit" and telling it "you tried to pay
yourself" call for completely different responses.

Checks run cheapest-first and the FIRST failure is reported. Reporting the
cheapest violated limit rather than all of them keeps the message actionable:
one number to change.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from urllib.parse import urlparse

from .config import Config
from .errors import CapExceeded

# Algorand addresses are 58 characters of RFC 4648 base32 with no padding.
_B32 = set(string.ascii_uppercase + "234567")


@dataclass(frozen=True)
class SpendContext:
    """What the caller knows about spending so far.

    Defaults are deliberately permissive: with no context, per-call limits and the
    allowlist still apply, and the running totals simply do not. That keeps a
    caller that has no ledger - a probe, a test - from silently losing the checks
    it can still perform.
    """

    spent_today_micro: int = 0
    session_remaining_micro: int | None = None


def looks_like_address(entry: str) -> bool:
    return len(entry) == 58 and set(entry) <= _B32


def allowlist_permits(allowlist: tuple[str, ...], pay_to: str, url: str) -> bool:
    """One list, two kinds of entry.

    Users think in both "this merchant" and "this site", so an entry is matched as
    an Algorand address when it looks like one and as a hostname otherwise.
    Hostname matching is exact and case-insensitive - no subdomain wildcards,
    because `evil-example.com` should never be permitted by an entry reading
    `example.com`.
    """
    if not allowlist:
        return True
    host = (urlparse(url).hostname or "").lower()
    for entry in allowlist:
        if looks_like_address(entry):
            if entry == pay_to:
                return True
        elif entry.strip().lower() == host:
            return True
    return False


def check(
    cfg: Config,
    *,
    amount_micro: int,
    pay_to: str,
    url: str,
    spend: SpendContext | None = None,
    max_price_micro: int | None = None,
) -> None:
    """Raise `CapExceeded` naming the first limit this payment would breach."""
    spend = spend or SpendContext()
    fmt = cfg.network.fmt

    if max_price_micro is not None and amount_micro > max_price_micro:
        raise CapExceeded(
            "max_price",
            f"Price {fmt(amount_micro)} exceeds the max_price of "
            f"{fmt(max_price_micro)} set for this call.",
        )

    if amount_micro > cfg.caps.per_call_micro:
        raise CapExceeded(
            "per_call",
            f"Price {fmt(amount_micro)} exceeds the per-call cap of "
            f"{fmt(cfg.caps.per_call_micro)}. Raise `caps.per_call_micro` in "
            "config.toml, or pass a higher max_price for one call.",
        )

    if cfg.caps.daily_micro is not None:
        would_total = spend.spent_today_micro + amount_micro
        if would_total > cfg.caps.daily_micro:
            raise CapExceeded(
                "daily",
                f"This payment of {fmt(amount_micro)} would take today's spend to "
                f"{fmt(would_total)}, over the daily cap of "
                f"{fmt(cfg.caps.daily_micro)}. Already spent today: "
                f"{fmt(spend.spent_today_micro)}. Resets at 00:00 UTC.",
            )

    if spend.session_remaining_micro is not None:
        if amount_micro > spend.session_remaining_micro:
            raise CapExceeded(
                "session_balance",
                f"Price {fmt(amount_micro)} exceeds the session's remaining "
                f"{fmt(spend.session_remaining_micro)}. The chain enforces this "
                "regardless; refusing here so the failure is legible instead of "
                "surfacing as a settlement error. Close the session and open a "
                "larger one.",
            )

    if not allowlist_permits(cfg.caps.allowlist, pay_to, url):
        raise CapExceeded(
            "allowlist",
            f"payTo {pay_to} and host {urlparse(url).hostname!r} are both outside "
            f"the allowlist ({len(cfg.caps.allowlist)} entries). Add either to "
            "`caps.allowlist` in config.toml, or empty the list to allow any "
            "merchant.",
        )
