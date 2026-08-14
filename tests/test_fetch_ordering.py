"""When the session opens, relative to everything that can refuse.

This is a regression test for a bug that cost real money on the common path, not
an adversarial one: `fetch` used to take an already-open session, so the caller
had to open and FUND one before the challenge was even read. A free URL that
answered 200 therefore moved $5 into a session account and spent none of it, and
so did every call refused by a cap.

The rule these tests pin: **the session provider is called only when a payment is
certain** - after the challenge is read, after every refusal, after every cap.
Asserted by making the provider explode, so "was it called" is unambiguous.

No network: httpx.MockTransport serves the challenges.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from obolus.config import load_config
from obolus.errors import CapExceeded, PaymentRefused
from obolus.x402 import fetch

TESTNET_CAIP2 = "algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI="
MERCHANT = "NJO3MQADL3UO236P75NAV4NCVFNA2SVVYH6BVUO5MFMIHBZVXNAQNNNFYI"
VAULT = "NJH7PU3LXJ2DYHQIZRHCDY5O4QW7HRIUQ5X3EO24VVF6MXA4BS6VNYYQFA"
URL = "https://merchant.example.com/thing"


class SessionOpened(Exception):
    """Raised by the provider, so a session open is impossible to miss."""


async def exploding_provider():
    raise SessionOpened


def challenge_header(amount: str = "50000", pay_to: str = MERCHANT) -> str:
    payload = {
        "x402Version": 2,
        "accepts": [
            {
                "scheme": "exact",
                "network": TESTNET_CAIP2,
                "asset": "10458941",
                "amount": amount,
                "payTo": pay_to,
                "maxTimeoutSeconds": 120,
                "extra": {"decimals": 6},
            }
        ],
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


def transport_for(status: int, *, amount: str = "50000", pay_to: str = MERCHANT):
    def handler(request: httpx.Request) -> httpx.Response:
        if status == 402:
            return httpx.Response(
                402, headers={"PAYMENT-REQUIRED": challenge_header(amount, pay_to)}
            )
        return httpx.Response(status, json={"free": True})

    return httpx.MockTransport(handler)


def cfg(tmp_path, toml: str = ""):
    if toml:
        (tmp_path / "config.toml").write_text(toml, encoding="utf-8")
    return load_config(network="testnet", data_dir=tmp_path)


# ---- the headline case ---------------------------------------------------


@pytest.mark.anyio
async def test_unpaid_url_never_opens_a_session(tmp_path):
    """The bug, stated as a test. A free URL must cost nothing at all.

    `x402_fetch` is documented as safe for URLs that may not be paid, so an agent
    using it as a general fetch tool must not fund an account by doing so.
    """
    result = await fetch(
        cfg(tmp_path),
        exploding_provider,
        URL,
        our_addresses={VAULT},
        transport=transport_for(200),
    )
    assert result.paid is False
    assert result.status_code == 200
    assert result.payer == "", "no session, so no payer"


# ---- refusals all precede the session ------------------------------------


@pytest.mark.anyio
async def test_price_over_cap_refuses_before_opening(tmp_path):
    with pytest.raises(CapExceeded) as exc:
        await fetch(
            cfg(tmp_path),
            exploding_provider,
            URL,
            max_price_micro=1_000,
            transport=transport_for(402),
        )
    assert exc.value.limit == "max_price"


@pytest.mark.anyio
async def test_per_call_cap_refuses_before_opening(tmp_path):
    with pytest.raises(CapExceeded):
        await fetch(
            cfg(tmp_path),
            exploding_provider,
            URL,
            transport=transport_for(402, amount="900000"),
        )


@pytest.mark.anyio
async def test_allowlist_refuses_before_opening(tmp_path):
    conf = cfg(tmp_path, '[caps]\nallowlist = ["someone.else.example"]\n')
    with pytest.raises(CapExceeded) as exc:
        await fetch(
            conf, exploding_provider, URL, transport=transport_for(402)
        )
    assert exc.value.limit == "allowlist"


@pytest.mark.anyio
async def test_self_payment_refuses_before_opening(tmp_path):
    """Known-ours addresses are checked without needing the session."""
    with pytest.raises(PaymentRefused, match="our own address"):
        await fetch(
            cfg(tmp_path),
            exploding_provider,
            URL,
            our_addresses={VAULT},
            transport=transport_for(402, pay_to=VAULT),
        )


@pytest.mark.anyio
async def test_unpayable_asset_refuses_before_opening(tmp_path):
    conf = cfg(tmp_path, "[networks.testnet]\npayment_asa = 31566704\n")
    with pytest.raises(PaymentRefused, match="No acceptable payment requirement"):
        await fetch(conf, exploding_provider, URL, transport=transport_for(402))


@pytest.mark.anyio
async def test_malformed_challenge_refuses_before_opening(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402)  # 402 with no challenge header

    with pytest.raises(PaymentRefused, match="not an x402 challenge"):
        await fetch(
            cfg(tmp_path),
            exploding_provider,
            URL,
            transport=httpx.MockTransport(handler),
        )


# ---- and it DOES open when a payment is real ------------------------------


@pytest.mark.anyio
async def test_acceptable_challenge_opens_the_session(tmp_path):
    """The other half: the provider must actually be reached when paying.

    Without this, every test above would also pass if `fetch` simply never opened
    a session at all.
    """
    with pytest.raises(SessionOpened):
        await fetch(
            cfg(tmp_path),
            exploding_provider,
            URL,
            our_addresses={VAULT},
            transport=transport_for(402),
        )


@pytest.mark.anyio
async def test_provider_is_called_once(tmp_path):
    calls = []

    async def counting_provider():
        calls.append(1)
        raise SessionOpened

    with pytest.raises(SessionOpened):
        await fetch(
            cfg(tmp_path), counting_provider, URL, transport=transport_for(402)
        )
    assert len(calls) == 1


@pytest.fixture
def anyio_backend():
    return "asyncio"
