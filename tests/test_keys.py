"""Key encodings, checked against algosdk rather than against our own arithmetic.

`keys.py` derives addresses with hand-rolled base32 and a sha512/256 checksum,
ported from the Authen node. That code is only trustworthy if it agrees with the
SDK that will actually submit the transactions — a self-consistent but wrong
encoding sends real ALGO to an address nobody holds the key for, and there is no
way back. So every assertion here compares against `algosdk`, not against a
literal we computed the same way.
"""

from __future__ import annotations

import os

import pytest
from algosdk import account as algo_account
from algosdk import encoding as algo_encoding
from algosdk import mnemonic as algo_mnemonic

from obolus.errors import WalletError
from obolus.keys import (
    Key,
    algorand_address,
    derive_session_key,
    key_from_seed,
    load_or_create_vault,
)


def test_address_matches_algosdk_for_random_seeds():
    for _ in range(50):
        key = key_from_seed(os.urandom(32))
        # Two independent SDK paths to the same address.
        assert key.address == algo_encoding.encode_address(key.public_bytes)
        assert key.address == algo_account.address_from_private_key(key.private_key)


def test_private_key_round_trips_through_mnemonic():
    """The seed must survive the export path a user would actually use."""
    key = key_from_seed(os.urandom(32))
    phrase = algo_mnemonic.from_private_key(key.private_key)
    assert algo_mnemonic.to_private_key(phrase) == key.private_key


def test_address_is_58_chars_and_decodes():
    key = key_from_seed(os.urandom(32))
    assert len(key.address) == 58
    assert algo_encoding.decode_address(key.address) == key.public_bytes
    assert algo_encoding.is_valid_address(key.address)


def test_seed_must_be_32_bytes():
    with pytest.raises(ValueError):
        key_from_seed(os.urandom(31))


def test_session_derivation_is_deterministic_and_distinct():
    """The property the reaper depends on: same vault + index -> same key, always.

    If this ever stops holding, orphaned session accounts become unreachable and
    the money in them is gone.
    """
    vault_seed = os.urandom(32)
    first = derive_session_key(vault_seed, 7)
    again = derive_session_key(vault_seed, 7)
    assert first.address == again.address
    assert first.seed == again.seed

    addresses = {derive_session_key(vault_seed, i).address for i in range(200)}
    assert len(addresses) == 200

    other_vault = derive_session_key(os.urandom(32), 7)
    assert other_vault.address != first.address


def test_session_key_does_not_reveal_the_vault():
    """A leaked session key must expose that session and nothing more."""
    vault_seed = os.urandom(32)
    session = derive_session_key(vault_seed, 1)
    assert vault_seed not in session.seed
    assert session.seed != vault_seed
    assert session.address != key_from_seed(vault_seed).address


def test_repr_never_leaks_the_seed():
    key = key_from_seed(os.urandom(32))
    assert key.seed.hex() not in repr(key)
    assert key.private_key not in repr(key)


def test_vault_seed_survives_a_write_read_cycle(tmp_path):
    """The O_BINARY regression, made explicit.

    On Windows without O_BINARY every 0x0A in the seed is written as 0x0D 0x0A, the
    file reads back 33+ bytes, and the wallet refuses to start — on roughly one
    install in eight, since a random 32-byte seed contains a newline about 12% of
    the time. Seeding the file with a value full of 0x0A makes that deterministic
    rather than a one-in-eight flake.
    """
    seed_path = tmp_path / "vault_seed.bin"
    key, created = load_or_create_vault(seed_path)
    assert created
    assert seed_path.stat().st_size == 32

    again, created_again = load_or_create_vault(seed_path)
    assert not created_again
    assert again.address == key.address

    # The pathological seed, written through the same path.
    newline_heavy = bytes([0x0A] * 32)
    forced = tmp_path / "forced_seed.bin"
    import obolus.keys as keys_mod

    original = os.urandom
    try:
        os.urandom = lambda n: newline_heavy[:n]  # noqa: ARG005
        keys_mod.os.urandom = os.urandom
        loaded, _ = load_or_create_vault(forced)
    finally:
        os.urandom = original
        keys_mod.os.urandom = original

    assert forced.stat().st_size == 32, "seed grew on write — O_BINARY is missing"
    assert loaded.seed == newline_heavy


def test_refuses_a_truncated_seed(tmp_path):
    seed_path = tmp_path / "vault_seed.bin"
    seed_path.write_bytes(os.urandom(31))
    with pytest.raises(WalletError) as exc:
        load_or_create_vault(seed_path)
    assert "32" in str(exc.value)


def test_key_is_frozen():
    key: Key = key_from_seed(os.urandom(32))
    with pytest.raises(Exception):
        key.seed = b"x" * 32  # type: ignore[misc]
