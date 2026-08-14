"""The signer, which is the piece that actually moves money.

The property that matters most is negative: it must NOT sign the facilitator's
fee-payer transaction. The payment group arrives with a transaction that belongs
to someone else, and signing it - or returning anything but None in its slot -
breaks settlement.
"""

from __future__ import annotations

import base64
import os

import pytest
from algosdk import encoding, transaction
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from obolus.keys import key_from_seed
from obolus.signer import SessionSigner


def _params():
    sp = transaction.SuggestedParams(
        fee=1000, first=1, last=1001, gh="SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=",
        flat_fee=True,
    )
    return sp


def _group(payer: str, fee_payer: str):
    """A payment group shaped like the one the facilitator builds."""
    sp = _params()
    axfer = transaction.AssetTransferTxn(
        sender=payer, sp=sp, receiver=fee_payer, amt=50_000, index=10458941
    )
    sponsor = transaction.PaymentTxn(sender=fee_payer, sp=sp, receiver=payer, amt=0)
    transaction.assign_group_id([axfer, sponsor])
    return [
        base64.b64decode(encoding.msgpack_encode(axfer)),
        base64.b64decode(encoding.msgpack_encode(sponsor)),
    ]


def test_signs_only_the_requested_index():
    session = key_from_seed(os.urandom(32))
    other = key_from_seed(os.urandom(32))
    unsigned = _group(session.address, other.address)

    signed = SessionSigner(session).sign_transactions(unsigned, [0])

    assert len(signed) == 2
    assert signed[0] is not None
    assert signed[1] is None, "the fee payer's transaction is not ours to sign"


def test_signature_is_valid_and_by_the_session_key():
    """Verified with nacl directly rather than through algosdk.

    Checking algosdk's signature with algosdk would only prove it is
    self-consistent. Algorand signs `b"TX" || msgpack(txn)`, so this reconstructs
    that preimage and checks it against the session's public key - the same thing
    a node does.
    """
    session = key_from_seed(os.urandom(32))
    other = key_from_seed(os.urandom(32))
    unsigned = _group(session.address, other.address)

    signed = SessionSigner(session).sign_transactions(unsigned, [0])
    decoded = encoding.msgpack_decode(base64.b64encode(signed[0]).decode())

    assert isinstance(decoded, transaction.SignedTransaction)
    assert decoded.transaction.sender == session.address

    preimage = b"TX" + base64.b64decode(encoding.msgpack_encode(decoded.transaction))
    VerifyKey(session.public_bytes).verify(preimage, base64.b64decode(decoded.signature))

    # Negative control: another key's public half must not verify it.
    with pytest.raises(BadSignatureError):
        VerifyKey(other.public_bytes).verify(
            preimage, base64.b64decode(decoded.signature)
        )


def test_signing_several_indexes_leaves_the_rest_alone():
    session = key_from_seed(os.urandom(32))
    unsigned = _group(session.address, session.address) + [
        _group(session.address, session.address)[0]
    ]
    signed = SessionSigner(session).sign_transactions(unsigned, [0, 2])
    assert signed[0] is not None
    assert signed[1] is None
    assert signed[2] is not None


def test_signing_nothing_returns_all_none():
    session = key_from_seed(os.urandom(32))
    unsigned = _group(session.address, session.address)
    assert SessionSigner(session).sign_transactions(unsigned, []) == [None, None]


def test_address_is_the_session_address():
    session = key_from_seed(os.urandom(32))
    assert SessionSigner(session).address == session.address


def test_signer_never_exposes_the_key():
    """The signer is handed to a third-party SDK; it must not carry a public seed."""
    session = key_from_seed(os.urandom(32))
    signer = SessionSigner(session)
    public_attrs = [a for a in dir(signer) if not a.startswith("_")]
    assert public_attrs == ["address", "sign_transactions"]
