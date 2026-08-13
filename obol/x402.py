"""The 402 flow: challenge, validate, sign, replay, receipt.

One public entry point, `fetch`. Give it a URL; if the server challenges, it pays
from the session account and returns the body it was after.

THREE THINGS IN HERE ARE NOT STYLE CHOICES
------------------------------------------

**The header is `PAYMENT-SIGNATURE`.** Not `PAYMENT`, not v1's `X-PAYMENT`. Send
the wrong name and the server sees no payment at all: it re-challenges with a
generic "Payment required" that is indistinguishable from a rejection, while the
facilitator's /verify happily reports isValid for the identical payload. This
cost real debugging time in the Authen build; see CLAUDE.md.

**We validate one requirement, then sign exactly that one.** A 402 may offer
several `accepts` entries. If we validated one and handed the whole challenge to
the SDK, the SDK's own selector could pick a different entry - a different payTo,
a different amount, a different asset - and every guard in this module would be
decorative. So the challenge is narrowed to the single validated entry before it
goes anywhere near `create_payment_payload`.

**The payload build runs in a thread.** P0.1 measured a ~900 ms event-loop stall
from the algod round trip inside `ExactAvmClientScheme.create_payment_payload`.
In an MCP server that stalls every other request, so it is quarantined in exactly
one `asyncio.to_thread` call and must stay that way.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import Config
from .keys import Key
from .signer import SessionSigner

CHALLENGE_HEADER = "PAYMENT-REQUIRED"
PAYMENT_HEADER = "PAYMENT-SIGNATURE"
RECEIPT_HEADERS = ("PAYMENT-RESPONSE", "X-PAYMENT-RESPONSE")
SCHEME_EXACT = "exact"


class PaymentRefused(Exception):
    """We declined to pay, before anything was signed.

    Every refusal names what it objected to. A wallet that says only "refused"
    forces the user to guess, and the guesses are expensive.
    """


class PaymentRejected(Exception):
    """We paid, or tried to, and the server did not serve the resource."""


@dataclass(frozen=True)
class Requirement:
    """One entry from `accepts[]`, decoded."""

    scheme: str
    network: str
    asset: int
    amount_micro: int
    pay_to: str
    max_timeout_seconds: int
    extra: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentResult:
    url: str
    status_code: int
    content: bytes
    content_type: str
    paid: bool
    price_micro: int = 0
    pay_to: str = ""
    txid: str = ""
    receipt: dict[str, Any] = field(default_factory=dict)
    payer: str = ""

    def json(self) -> Any:
        return json.loads(self.content)


def _decode_b64_json(value: str) -> dict[str, Any]:
    return json.loads(base64.b64decode(value))


def parse_challenge(response: httpx.Response) -> dict[str, Any]:
    """Decode the 402 challenge, or refuse.

    A malformed challenge is refused rather than interpreted. Guessing at the
    shape of a payment demand is how money reaches the wrong address.
    """
    raw = response.headers.get(CHALLENGE_HEADER)
    if not raw:
        raise PaymentRefused(
            f"Server answered {response.status_code} without a {CHALLENGE_HEADER} "
            "header. That is not an x402 challenge, so there is nothing to pay."
        )
    try:
        challenge = _decode_b64_json(raw)
    except Exception as exc:
        raise PaymentRefused(f"{CHALLENGE_HEADER} is not base64 JSON: {exc}") from exc

    if not isinstance(challenge.get("accepts"), list) or not challenge["accepts"]:
        raise PaymentRefused("Challenge carries no `accepts` entries.")
    if int(challenge.get("x402Version", 0)) != 2:
        raise PaymentRefused(
            f"Challenge is x402 version {challenge.get('x402Version')!r}; this "
            "client speaks version 2 only."
        )
    return challenge


def choose_requirement(challenge: dict[str, Any], cfg: Config) -> Requirement:
    """Pick the one `accepts` entry we are willing to pay, or explain why none.

    Matching is on scheme, network and asset together. A near-miss is reported
    with what was offered against what we can pay, because "no matching
    requirement" on its own sends people to read SDK source.
    """
    offered: list[str] = []
    for entry in challenge["accepts"]:
        try:
            requirement = Requirement(
                scheme=str(entry.get("scheme", "")),
                network=str(entry.get("network", "")),
                asset=int(entry.get("asset", -1)),
                amount_micro=int(entry.get("amount", -1)),
                pay_to=str(entry.get("payTo", "")),
                max_timeout_seconds=int(entry.get("maxTimeoutSeconds", 60)),
                extra=entry.get("extra") or {},
                raw=entry,
            )
        except (TypeError, ValueError) as exc:
            offered.append(f"unparseable entry ({exc})")
            continue

        offered.append(
            f"{requirement.scheme}/{requirement.network}/asset {requirement.asset}"
        )
        if (
            requirement.scheme == SCHEME_EXACT
            and requirement.network == cfg.network.caip2
            and requirement.asset == cfg.network.payment_asa
        ):
            return requirement

    raise PaymentRefused(
        "No acceptable payment requirement.\n"
        f"  offered:  {'; '.join(offered)}\n"
        f"  we pay:   {SCHEME_EXACT}/{cfg.network.caip2}/asset "
        f"{cfg.network.payment_asa}\n"
        "The asset is a network-profile setting, not a constant - if this is the "
        "right resource on the right chain, check `payment_asa` in config.toml."
    )


def guard(
    requirement: Requirement,
    cfg: Config,
    url: str,
    our_addresses: set[str],
    max_price_micro: int | None = None,
) -> None:
    """The refusals that are not caps. None of these has a config key.

    Caps are the user's to set; these hold whatever the budget says. See
    DESIGN.md section 7.
    """
    if requirement.pay_to in our_addresses:
        raise PaymentRefused(
            f"payTo {requirement.pay_to} is our own address. Paying yourself is "
            "not a payment - it is a round trip on chain, and the first thing an "
            "anti-wash review looks for."
        )

    if cfg.network.is_mainnet and not url.lower().startswith("https://"):
        raise PaymentRefused(
            f"Refusing to pay a non-https resource on mainnet: {url}\n"
            "The facilitator auto-catalogs the resource URL permanently on "
            "/verify, keyed to the payTo. About 13% of the live index is loopback "
            "junk created exactly this way."
        )

    if requirement.amount_micro < 0:
        raise PaymentRefused("Challenge does not state a valid amount.")

    # The explicit per-call ceiling from the caller, and the configured one. The
    # rest of the spend controls - daily totals, allowlists, the consent model -
    # land in caps.py in Phase 3.
    if max_price_micro is not None and requirement.amount_micro > max_price_micro:
        raise PaymentRefused(
            f"Price {cfg.network.fmt(requirement.amount_micro)} exceeds the "
            f"max_price of {cfg.network.fmt(max_price_micro)} for this call."
        )
    if requirement.amount_micro > cfg.caps.per_call_micro:
        raise PaymentRefused(
            f"Price {cfg.network.fmt(requirement.amount_micro)} exceeds the "
            f"per-call cap of {cfg.network.fmt(cfg.caps.per_call_micro)}."
        )


def _narrow(challenge: dict[str, Any], requirement: Requirement) -> dict[str, Any]:
    """Reduce the challenge to the single requirement we validated.

    Without this the SDK's selector chooses among `accepts` itself, and could sign
    an entry the guards never saw.
    """
    return {**challenge, "accepts": [requirement.raw]}


def _build_payment_header(
    challenge: dict[str, Any], cfg: Config, session: Key
) -> str:
    """Build and sign the payment payload. BLOCKS - always call via to_thread.

    Uses the synchronous client deliberately (P0.1): the async one offers nothing
    here, since the AVM scheme is synchronous either way, and awaiting it parks
    the event loop for the algod round trip.
    """
    from x402.client import x402ClientSync
    from x402.mechanisms.avm.exact import register_exact_avm_client
    from x402.schemas.payments import PaymentRequired

    client = x402ClientSync()
    register_exact_avm_client(
        client,
        signer=SessionSigner(session),
        networks=cfg.network.caip2,
        algod_url=cfg.network.algod_url,
    )
    payload = client.create_payment_payload(PaymentRequired.model_validate(challenge))
    return base64.b64encode(
        payload.model_dump_json(by_alias=True, exclude_none=True).encode()
    ).decode()


def _read_receipt(response: httpx.Response) -> dict[str, Any]:
    for name in RECEIPT_HEADERS:
        if raw := response.headers.get(name):
            return _decode_b64_json(raw)
    return {}


async def fetch(
    cfg: Config,
    session: Key,
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    max_price_micro: int | None = None,
    our_addresses: set[str] | None = None,
    timeout: float = 90.0,
) -> PaymentResult:
    """Fetch a URL, paying if it challenges.

    An unpaid 200 is a perfectly good outcome and costs nothing - not every URL
    behind this call is paid, and we should not insist on spending.
    """
    method = method.upper()
    our = set(our_addresses or ()) | {session.address}

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as http:
        first = await http.request(method, url, content=body, headers=headers)
        if first.status_code != 402:
            return PaymentResult(
                url=url,
                status_code=first.status_code,
                content=first.content,
                content_type=first.headers.get("content-type", ""),
                paid=False,
            )

        challenge = parse_challenge(first)
        requirement = choose_requirement(challenge, cfg)
        guard(requirement, cfg, url, our, max_price_micro)

        # The one blocking call in the flow, quarantined. See P0.1.
        payment_header = await asyncio.to_thread(
            _build_payment_header, _narrow(challenge, requirement), cfg, session
        )

        replay_headers = {**(headers or {}), PAYMENT_HEADER: payment_header}
        second = await http.request(
            method, url, content=body, headers=replay_headers
        )

        if second.status_code != 200:
            # The reason rides in a re-issued challenge header, not the body -
            # the body is typically empty. Decode it or you are guessing.
            detail = ""
            if again := second.headers.get(CHALLENGE_HEADER):
                try:
                    detail = json.dumps(_decode_b64_json(again))[:600]
                except Exception:
                    detail = again[:200]
            raise PaymentRejected(
                f"Paid request returned {second.status_code}.\n"
                f"  re-issued challenge: {detail or '(none)'}\n"
                f"  body: {second.text[:300]!r}"
            )

        receipt = _read_receipt(second)
        if not receipt:
            raise PaymentRejected(
                "Server served the resource but returned no settlement receipt. "
                "Without one a buyer cannot prove payment."
            )

        return PaymentResult(
            url=url,
            status_code=second.status_code,
            content=second.content,
            content_type=second.headers.get("content-type", ""),
            paid=True,
            price_micro=requirement.amount_micro,
            pay_to=requirement.pay_to,
            txid=str(receipt.get("transaction") or receipt.get("txHash") or ""),
            receipt=receipt,
            payer=session.address,
        )
