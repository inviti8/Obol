"""The vault key, and the session keys derived from it.

Ported from `D:/repos/Authen/authen/keys.py` rather than reinvented - the atomic
write and its `O_BINARY` flag are the expensive part, and the reasoning is
preserved in the comments below because it is not guessable from the code.

Two departures from the Authen original:

**Session keys are derived, not generated.** `DESIGN.md` section 2 originally said a
session keypair was memory-only with just the address persisted. That cannot work:
sweeping an orphaned session means signing `close_assets_to` and
`close_remainder_to` FROM the session account, so an address alone leaves the
funds stranded forever - exactly the silent loss the reaper exists to prevent.
Deriving each session key from the vault seed fixes it without weakening anything:

    session_seed = HMAC-SHA256(vault_seed, "obol-session-v1" || index)

The ledger stores an index and an address; no session key is ever written to disk;
and HMAC is one-way, so a leaked session key still exposes only that session's
balance and says nothing about the vault. See DESIGN.md section 2.

**Keys come out in algosdk's format.** `nacl` is what algosdk itself uses, so the
seed -> private key -> address path here is byte-identical to
`algosdk.account.generate_account()` and the two are interchangeable.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from pathlib import Path

from .errors import WalletError
from nacl.signing import SigningKey

SEED_FILENAME = "vault_seed.bin"
SESSION_INFO = b"obol-session-v1"


def _b32_nopad(b: bytes) -> str:
    return base64.b32encode(b).decode("ascii").rstrip("=")


def algorand_address(pub32: bytes) -> str:
    """Algorand address: base32(pubkey || last 4 bytes of sha512/256(pubkey))."""
    checksum = hashlib.new("sha512_256", pub32).digest()[-4:]
    return _b32_nopad(pub32 + checksum)


def stellar_address(pub32: bytes) -> str:
    """Stellar strkey: base32(version || pubkey || crc16-xmodem), version 6<<3.

    Unused by v1 - Algorand only - but the same 32 bytes are already a Stellar
    address, and `CLAUDE.md` keeps Stellar deferred rather than dropped. Costs
    nothing to carry and proves the key layer is chain-agnostic.
    """
    payload = bytes([6 << 3]) + pub32
    crc = 0
    for byte in payload:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return _b32_nopad(payload + crc.to_bytes(2, "little"))


@dataclass(frozen=True)
class Key:
    """An Ed25519 keypair in the shapes algosdk and the chain want.

    `seed` never leaves the process and is never logged. `private_key` is the
    base64 form algosdk's `txn.sign()` expects.
    """

    seed: bytes
    public_bytes: bytes

    @property
    def private_key(self) -> str:
        return base64.b64encode(self.seed + self.public_bytes).decode("ascii")

    @property
    def address(self) -> str:
        return algorand_address(self.public_bytes)

    @property
    def stellar(self) -> str:
        return stellar_address(self.public_bytes)

    def __repr__(self) -> str:  # never print the seed, even by accident
        return f"Key(address={self.address})"


def key_from_seed(seed: bytes) -> Key:
    if len(seed) != 32:
        raise ValueError(f"Seed is {len(seed)} bytes, expected 32.")
    return Key(seed=seed, public_bytes=bytes(SigningKey(seed).verify_key))


def derive_session_key(vault_seed: bytes, index: int) -> Key:
    """The session key for `index`, derived deterministically from the vault seed.

    A pure HMAC expansion. The vault seed is already 32 uniformly random bytes, so
    an HKDF-Extract step would add nothing - RFC 5869 section 3.3 says as much.

    This is what makes the reaper possible: after a crash the ledger's index is
    enough to regenerate the key and close the account.
    """
    if index < 0:
        raise ValueError("Session index must be non-negative.")
    msg = SESSION_INFO + index.to_bytes(8, "big")
    return key_from_seed(hmac.new(vault_seed, msg, hashlib.sha256).digest())


def load_or_create_vault(seed_path: Path) -> tuple[Key, bool]:
    """Load the vault key, generating it on first run. Returns (key, created).

    v1 stores the seed in a 0600 file. `keyring` and an OS keychain are the
    finished-product backend and go behind this same function - nothing above this
    module knows or cares which is in use.
    """
    seed_path.parent.mkdir(parents=True, exist_ok=True)

    if seed_path.exists():
        seed = seed_path.read_bytes()
        if len(seed) != 32:
            raise WalletError(
                f"Vault seed at {seed_path} is {len(seed)} bytes, expected 32.\n"
                "Refusing to start rather than sign with a malformed key. If this "
                "install never held funds, delete the file and a new vault will be "
                "generated - but check the address first, because the money is on "
                "chain and the seed is the only way back to it."
            )
        return key_from_seed(seed), False

    seed = os.urandom(32)
    # Write to a temp file, fsync, then atomically rename.
    #
    # Writing straight to the destination creates a zero-byte file before the
    # content lands. A crash in that window leaves a truncated seed, and the length
    # check above then refuses to start FOREVER - the wallet cannot regenerate (the
    # file exists) and cannot proceed (it is malformed). Rename is atomic, so the
    # destination either does not exist or holds all 32 bytes.
    #
    # Mode is set at creation rather than by a later chmod, so the seed is never
    # briefly world readable.
    #
    # O_BINARY is REQUIRED on Windows and absent on POSIX. Without it os.open uses
    # text mode, which expands every 0x0A in the seed to 0x0D 0x0A - the file comes
    # back 33+ bytes and the wallet refuses to start. A random 32-byte seed contains
    # at least one 0x0A about 12% of the time, so it presents as an intermittent
    # failure on roughly one install in eight.
    binary = getattr(os, "O_BINARY", 0)
    tmp = seed_path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | binary, 0o600)
    try:
        os.write(fd, seed)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, seed_path)
    return key_from_seed(seed), True
