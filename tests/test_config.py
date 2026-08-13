"""Config resolution, and the invariants that keep money on the right chain.

Two of these are not style checks. Defaulting to testnet is what stops a
forgotten flag spending real USDC, and per-network ledger paths are what stops
the reaper deriving a mainnet session key for a testnet record.
"""

from __future__ import annotations

import pytest

from obol.config import PROFILES, Caps, load_config
from obol.errors import WalletError


def test_defaults_to_testnet(tmp_path, monkeypatch):
    monkeypatch.delenv("OBOL_NETWORK", raising=False)
    cfg = load_config(data_dir=tmp_path)
    assert cfg.network.name == "testnet"
    assert not cfg.network.is_mainnet


def test_env_selects_the_network(tmp_path, monkeypatch):
    monkeypatch.setenv("OBOL_NETWORK", "mainnet")
    assert load_config(data_dir=tmp_path).network.name == "mainnet"


def test_explicit_argument_beats_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OBOL_NETWORK", "mainnet")
    cfg = load_config(network="testnet", data_dir=tmp_path)
    assert cfg.network.name == "testnet"


def test_unknown_network_refuses(tmp_path):
    with pytest.raises(WalletError):
        load_config(network="algorand-betanet-maybe", data_dir=tmp_path)


def test_known_asset_ids():
    """Pinned because getting these wrong sends money to an asset nobody wants."""
    assert PROFILES["mainnet"].payment_asa == 31566704
    assert PROFILES["testnet"].payment_asa == 10458941
    assert PROFILES["mainnet"].caip2.startswith("algorand:")
    assert PROFILES["mainnet"].slug == "algorand-mainnet"


def test_ledger_path_is_per_network(tmp_path):
    """A testnet session address means nothing on mainnet.

    Sharing one ledger would have the reaper deriving keys for accounts that do
    not exist on the active chain, and reporting failures for all of them.
    """
    testnet = load_config(network="testnet", data_dir=tmp_path)
    mainnet = load_config(network="mainnet", data_dir=tmp_path)
    assert testnet.ledger_path != mainnet.ledger_path


def test_seed_path_is_shared(tmp_path):
    """One key is one address on both chains; two vaults would double bootstrap."""
    testnet = load_config(network="testnet", data_dir=tmp_path)
    mainnet = load_config(network="mainnet", data_dir=tmp_path)
    assert testnet.seed_path == mainnet.seed_path


def test_toml_overrides_the_payment_asset(tmp_path):
    """The stand-in-ASA case: a testnet rail may not run real testnet USDC."""
    (tmp_path / "config.toml").write_text(
        '[networks.testnet]\npayment_asa = 769120200\n', encoding="utf-8"
    )
    cfg = load_config(network="testnet", data_dir=tmp_path)
    assert cfg.network.payment_asa == 769120200
    # An override must not bleed into the other profile.
    assert PROFILES["testnet"].payment_asa == 10458941


def test_toml_overrides_caps(tmp_path):
    (tmp_path / "config.toml").write_text(
        "[caps]\nper_call_micro = 10000\ndaily_micro = 250000\n", encoding="utf-8"
    )
    cfg = load_config(network="testnet", data_dir=tmp_path)
    assert cfg.caps.per_call_micro == 10_000
    assert cfg.caps.daily_micro == 250_000


def test_cap_defaults():
    caps = Caps()
    assert caps.per_call_micro == 500_000       # $0.50
    assert caps.session_balance_micro == 5_000_000  # $5
    assert caps.daily_micro is None             # off unless set
    assert caps.allowlist == ()                 # off unless set


def test_unit_conversion_round_trips():
    profile = PROFILES["testnet"]
    assert profile.to_units(1.5) == 1_500_000
    assert profile.to_units(0.05) == 50_000
    assert profile.fmt(50_000) == "0.050000"


def test_no_float_drift_on_awkward_amounts():
    """0.07 is not representable in binary; rounding must not lose a microunit."""
    profile = PROFILES["testnet"]
    for whole, expected in ((0.07, 70_000), (0.29, 290_000), (1.11, 1_110_000)):
        assert profile.to_units(whole) == expected
