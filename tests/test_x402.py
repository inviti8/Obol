"""The 402 flow's decisions, tested without a network or a key.

Everything here happens BEFORE anything is signed. These are the checks that
decide whether money moves and where it goes, so they are tested on their own
rather than only through a live payment.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from obol.config import load_config
from obol.x402 import (
    PaymentRefused,
    PaymentResult,
    _narrow,
    choose_requirement,
    guard,
    parse_challenge,
)

MAINNET_CAIP2 = "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8="
TESTNET_CAIP2 = "algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI="
MERCHANT = "NJO3MQADL3UO236P75NAV4NCVFNA2SVVYH6BVUO5MFMIHBZVXNAQNNNFYI"
OURS = "RKCLWLQTCB5ZAER647M3CTDZ7TPYAUMSSX6FPI767J43R3RP3PEGTJDOJA"


def cfg(tmp_path, network="testnet"):
    return load_config(network=network, data_dir=tmp_path)


def accept(
    *, asset=10458941, amount="50000", network=TESTNET_CAIP2, pay_to=MERCHANT, scheme="exact"
):
    return {
        "scheme": scheme,
        "network": network,
        "asset": str(asset),
        "amount": amount,
        "payTo": pay_to,
        "maxTimeoutSeconds": 120,
        "extra": {"tag": "test-tag", "decimals": 6},
    }


def challenge(*accepts):
    return {"x402Version": 2, "error": "Payment required", "accepts": list(accepts)}


def response_402(payload=None, *, header=True, status=402):
    headers = {}
    if header:
        raw = base64.b64encode(json.dumps(payload).encode()).decode()
        headers["PAYMENT-REQUIRED"] = raw
    return httpx.Response(status, headers=headers, request=httpx.Request("GET", "http://x"))


# ---- challenge parsing ---------------------------------------------------


def test_parses_a_well_formed_challenge():
    parsed = parse_challenge(response_402(challenge(accept())))
    assert parsed["x402Version"] == 2
    assert len(parsed["accepts"]) == 1


def test_refuses_a_402_with_no_challenge_header():
    """Not every 402 is an x402 challenge. There is nothing to pay."""
    with pytest.raises(PaymentRefused, match="without a PAYMENT-REQUIRED"):
        parse_challenge(response_402(header=False))


def test_refuses_a_challenge_that_is_not_base64_json():
    resp = httpx.Response(
        402, headers={"PAYMENT-REQUIRED": "!!!not base64!!!"},
        request=httpx.Request("GET", "http://x"),
    )
    with pytest.raises(PaymentRefused, match="not base64 JSON"):
        parse_challenge(resp)


def test_refuses_a_challenge_with_no_accepts():
    with pytest.raises(PaymentRefused, match="no `accepts`"):
        parse_challenge(response_402({"x402Version": 2, "accepts": []}))


def test_refuses_x402_version_1():
    """v1 uses a different header name entirely; guessing is how money is lost."""
    with pytest.raises(PaymentRefused, match="version 2 only"):
        parse_challenge(response_402({"x402Version": 1, "accepts": [accept()]}))


# ---- requirement selection ----------------------------------------------


def test_selects_the_matching_requirement_among_several(tmp_path):
    """The point of selection: several offers, exactly one we can pay."""
    wrong_asset = accept(asset=31566704)
    wrong_network = accept(network=MAINNET_CAIP2)
    right = accept(pay_to=MERCHANT, amount="1234")
    chosen = choose_requirement(
        challenge(wrong_asset, wrong_network, right), cfg(tmp_path)
    )
    assert chosen.amount_micro == 1234
    assert chosen.asset == 10458941
    assert chosen.network == TESTNET_CAIP2


def test_refuses_when_the_asset_does_not_match(tmp_path):
    """The stand-in-ASA trap: right chain, right scheme, wrong asset."""
    with pytest.raises(PaymentRefused) as exc:
        choose_requirement(challenge(accept(asset=769120200)), cfg(tmp_path))
    message = str(exc.value)
    assert "769120200" in message and "10458941" in message, "name both ids"
    assert "payment_asa" in message, "point at the setting that fixes it"


def test_refuses_a_mainnet_challenge_while_on_testnet(tmp_path):
    with pytest.raises(PaymentRefused, match="No acceptable payment requirement"):
        choose_requirement(challenge(accept(network=MAINNET_CAIP2)), cfg(tmp_path))


def test_refuses_an_unknown_scheme(tmp_path):
    with pytest.raises(PaymentRefused):
        choose_requirement(challenge(accept(scheme="upto")), cfg(tmp_path))


# ---- the refusals that are not caps --------------------------------------


def test_refuses_to_pay_ourselves(tmp_path):
    """A round trip on chain, and the first thing an anti-wash review looks for."""
    requirement = choose_requirement(challenge(accept(pay_to=OURS)), cfg(tmp_path))
    with pytest.raises(PaymentRefused, match="our own address"):
        guard(requirement, cfg(tmp_path), "http://x/y", {OURS})


def test_refuses_to_pay_our_vault_too(tmp_path):
    vault = "NJH7PU3LXJ2DYHQIZRHCDY5O4QW7HRIUQ5X3EO24VVF6MXA4BS6VNYYQFA"
    requirement = choose_requirement(challenge(accept(pay_to=vault)), cfg(tmp_path))
    with pytest.raises(PaymentRefused, match="our own address"):
        guard(requirement, cfg(tmp_path), "http://x/y", {OURS, vault})


def test_refuses_non_https_on_mainnet(tmp_path):
    """The URL in the challenge is catalogued permanently, keyed to the payTo."""
    conf = cfg(tmp_path, "mainnet")
    requirement = choose_requirement(
        challenge(accept(asset=31566704, network=MAINNET_CAIP2)), conf
    )
    with pytest.raises(PaymentRefused, match="non-https"):
        guard(requirement, conf, "http://127.0.0.1:8402/api", {OURS})


def test_allows_loopback_on_testnet(tmp_path):
    """Testnet development happens on loopback; the rule is mainnet-only."""
    conf = cfg(tmp_path)
    requirement = choose_requirement(challenge(accept()), conf)
    guard(requirement, conf, "http://127.0.0.1:8402/api", {OURS})


def test_guard_does_not_judge_price(tmp_path):
    """Price is a cap, and caps live in caps.py.

    Asserted rather than assumed, because the split is the whole reason a caller
    can tell "raise your limit" apart from "you tried to pay yourself". If price
    checks crept back in here they would raise PaymentRefused without a `limit`,
    and the caller would lose that distinction silently.
    """
    conf = cfg(tmp_path)
    absurd = choose_requirement(challenge(accept(amount="99000000")), conf)
    guard(absurd, conf, "http://x/y", {OURS})  # must not raise


def test_refuses_a_negative_amount(tmp_path):
    conf = cfg(tmp_path)
    requirement = choose_requirement(challenge(accept(amount="-1")), conf)
    with pytest.raises(PaymentRefused, match="valid amount"):
        guard(requirement, conf, "http://x/y", {OURS})


# ---- validate-then-sign --------------------------------------------------


def test_narrow_leaves_only_the_validated_requirement(tmp_path):
    """Without this the SDK could sign an entry the guards never saw.

    Validating accepts[2] and then handing over all three would make every check
    in this module decorative - the SDK runs its own selector.
    """
    full = challenge(accept(asset=31566704), accept(amount="777"), accept(scheme="upto"))
    chosen = choose_requirement(full, cfg(tmp_path))
    narrowed = _narrow(full, chosen)
    assert len(narrowed["accepts"]) == 1
    assert narrowed["accepts"][0]["amount"] == "777"
    assert narrowed["x402Version"] == 2
    # The original is untouched, so nothing downstream sees a mutated challenge.
    assert len(full["accepts"]) == 3


# ---- result ---------------------------------------------------------------


def test_result_decodes_json_body():
    result = PaymentResult(
        url="http://x", status_code=200, content=b'{"a":1}',
        content_type="application/json", paid=True,
    )
    assert result.json() == {"a": 1}


def test_unpaid_result_defaults_to_zero_spend():
    result = PaymentResult(
        url="http://x", status_code=200, content=b"hi", content_type="text/plain",
        paid=False,
    )
    assert result.price_micro == 0
    assert result.txid == ""
