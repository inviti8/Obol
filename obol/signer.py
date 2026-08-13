"""The x402 `ClientAvmSigner` implementation, over a session key.

Ported near-verbatim from `D:/repos/Authen/tools/pay_once.py`, which drove this
protocol end to end twice. Two properties of the port are load-bearing.

**It signs only the indexes it is asked to sign.** The payment group contains a
fee-payer transaction that belongs to the facilitator; signing it, or returning
anything but `None` in its slot, breaks settlement. This is why the method takes
`indexes_to_sign` at all.

**It stays synchronous.** P0.1 established that the AVM scheme calls
`sign_transactions` directly rather than through an await, even under the async
client, so there is no async variant to write and nothing here may block on I/O.

The signer holds a session key and never the vault key. That is the whole point
of the two-tier design in DESIGN.md section 2: the vault key is never handed to an
x402 SDK and is never in a request path.
"""

from __future__ import annotations

import base64

from algosdk import encoding

from .keys import Key


class SessionSigner:
    """Signs payment transactions for one session account."""

    def __init__(self, key: Key) -> None:
        self._key = key

    @property
    def address(self) -> str:
        return self._key.address

    def sign_transactions(
        self, unsigned_txns: list[bytes], indexes_to_sign: list[int]
    ) -> list[bytes | None]:
        """Sign the requested group indexes, leaving every other slot None.

        The round trip through msgpack looks redundant and is not: the SDK hands
        over raw encoded transaction bytes, and `algosdk` will only sign a decoded
        `Transaction` object.

        The bytes returned here are passed through opaquely and base64'd by the SDK
        without inspection - no `sig`/`lsig` special-casing anywhere in the path,
        which is the fact that keeps the LogicSig option in DESIGN.md section 6 open.
        """
        out: list[bytes | None] = [None] * len(unsigned_txns)
        for i in indexes_to_sign:
            txn = encoding.msgpack_decode(base64.b64encode(unsigned_txns[i]).decode())
            out[i] = base64.b64decode(
                encoding.msgpack_encode(txn.sign(self._key.private_key))
            )
        return out
