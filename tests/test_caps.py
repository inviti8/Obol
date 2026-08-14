"""Spend controls. Every one of these runs without a network or a key.

Two things are asserted throughout and are easy to lose: each refusal names WHICH
limit was hit (`exc.limit`), and the cheapest violated limit is the one reported,
so the user gets one number to change rather than a list.
"""

from __future__ import annotations

import pytest

from obolus.caps import SpendContext, allowlist_permits, check, looks_like_address
from obolus.config import load_config
from obolus.errors import CapExceeded, PaymentRefused

MERCHANT = "NJO3MQADL3UO236P75NAV4NCVFNA2SVVYH6BVUO5MFMIHBZVXNAQNNNFYI"
OTHER = "RKCLWLQTCB5ZAER647M3CTDZ7TPYAUMSSX6FPI767J43R3RP3PEGTJDOJA"
URL = "https://merchant.example.com/api/thing"


def cfg(tmp_path, toml: str = ""):
    if toml:
        (tmp_path / "config.toml").write_text(toml, encoding="utf-8")
    return load_config(network="testnet", data_dir=tmp_path)


def pay(conf, amount, **kw):
    kw.setdefault("pay_to", MERCHANT)
    kw.setdefault("url", URL)
    check(conf, amount_micro=amount, **kw)


# ---- the happy path ------------------------------------------------------


def test_allows_a_payment_inside_every_limit(tmp_path):
    pay(cfg(tmp_path), 50_000)


def test_no_context_still_applies_per_call_limits(tmp_path):
    """Omitting SpendContext must not silently disable the checks it can run."""
    with pytest.raises(CapExceeded) as exc:
        pay(cfg(tmp_path), 900_000)
    assert exc.value.limit == "per_call"


# ---- per-call ------------------------------------------------------------


def test_explicit_max_price_refuses(tmp_path):
    with pytest.raises(CapExceeded) as exc:
        pay(cfg(tmp_path), 50_000, max_price_micro=10_000)
    assert exc.value.limit == "max_price"
    assert "0.050000" in str(exc.value) and "0.010000" in str(exc.value)


def test_configured_per_call_cap_refuses(tmp_path):
    with pytest.raises(CapExceeded) as exc:
        pay(cfg(tmp_path), 600_000)
    assert exc.value.limit == "per_call"
    assert "per_call_micro" in str(exc.value), "name the setting that fixes it"


def test_max_price_is_reported_before_per_call(tmp_path):
    """Cheapest-first: the caller's own limit is the one they can change now."""
    with pytest.raises(CapExceeded) as exc:
        pay(cfg(tmp_path), 900_000, max_price_micro=10_000)
    assert exc.value.limit == "max_price"


def test_price_exactly_at_the_cap_is_allowed(tmp_path):
    pay(cfg(tmp_path), 500_000)
    pay(cfg(tmp_path), 50_000, max_price_micro=50_000)


# ---- daily ---------------------------------------------------------------


def test_daily_cap_counts_prior_spend(tmp_path):
    conf = cfg(tmp_path, "[caps]\ndaily_micro = 100000\n")
    pay(conf, 50_000, spend=SpendContext(spent_today_micro=40_000))
    with pytest.raises(CapExceeded) as exc:
        pay(conf, 50_000, spend=SpendContext(spent_today_micro=60_000))
    assert exc.value.limit == "daily"
    assert "0.060000" in str(exc.value), "show what has already gone"
    assert "UTC" in str(exc.value), "say when it resets"


def test_daily_cap_is_off_by_default(tmp_path):
    conf = cfg(tmp_path)
    assert conf.caps.daily_micro is None
    pay(conf, 500_000, spend=SpendContext(spent_today_micro=99_000_000))


def test_daily_cap_boundary_is_inclusive(tmp_path):
    conf = cfg(tmp_path, "[caps]\ndaily_micro = 100000\n")
    pay(conf, 50_000, spend=SpendContext(spent_today_micro=50_000))
    with pytest.raises(CapExceeded):
        pay(conf, 50_001, spend=SpendContext(spent_today_micro=50_000))


# ---- session balance -----------------------------------------------------


def test_session_remaining_refuses_legibly(tmp_path):
    """The chain enforces this anyway; we refuse so the error is readable."""
    conf = cfg(tmp_path)
    with pytest.raises(CapExceeded) as exc:
        pay(conf, 50_000, spend=SpendContext(session_remaining_micro=10_000))
    assert exc.value.limit == "session_balance"
    assert "chain enforces" in str(exc.value)


def test_session_remaining_allows_the_exact_balance(tmp_path):
    pay(cfg(tmp_path), 50_000, spend=SpendContext(session_remaining_micro=50_000))


def test_unknown_session_remaining_does_not_refuse(tmp_path):
    pay(cfg(tmp_path), 50_000, spend=SpendContext(session_remaining_micro=None))


# ---- allowlist -----------------------------------------------------------


def test_allowlist_is_off_when_empty(tmp_path):
    pay(cfg(tmp_path), 50_000)


def test_allowlist_permits_a_listed_payto(tmp_path):
    conf = cfg(tmp_path, f'[caps]\nallowlist = ["{MERCHANT}"]\n')
    pay(conf, 50_000)


def test_allowlist_permits_a_listed_host(tmp_path):
    conf = cfg(tmp_path, '[caps]\nallowlist = ["merchant.example.com"]\n')
    pay(conf, 50_000)


def test_allowlist_refuses_an_unlisted_merchant(tmp_path):
    conf = cfg(tmp_path, f'[caps]\nallowlist = ["{OTHER}"]\n')
    with pytest.raises(CapExceeded) as exc:
        pay(conf, 50_000)
    assert exc.value.limit == "allowlist"


def test_allowlist_host_match_is_exact_not_suffix():
    """`evil-example.com` must never be permitted by an entry `example.com`."""
    assert not allowlist_permits(
        ("example.com",), MERCHANT, "https://evil-example.com/x"
    )
    assert not allowlist_permits(
        ("example.com",), MERCHANT, "https://sub.example.com/x"
    )
    assert allowlist_permits(("example.com",), MERCHANT, "https://EXAMPLE.com/x")


def test_address_detection():
    assert looks_like_address(MERCHANT)
    assert not looks_like_address("merchant.example.com")
    assert not looks_like_address(MERCHANT[:57])
    assert not looks_like_address(MERCHANT.lower())


# ---- exception contract --------------------------------------------------


def test_cap_exceeded_is_a_payment_refused(tmp_path):
    """`except PaymentRefused` must keep catching caps.

    Callers that only care whether money moved should not have to enumerate
    subclasses.
    """
    with pytest.raises(PaymentRefused):
        pay(cfg(tmp_path), 900_000)
