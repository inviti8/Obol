"""Buy an attestation through Obolus, then verify it with nothing but its bytes.

    uv run python probes/verify_attestation.py

This is Phase 2's done-condition, and it is deliberately end to end: pay through
`obolus.x402.fetch`, then check the result offline against the node's published key
without asking the node to confirm anything about it.

WHY OFFLINE VERIFICATION IS THE POINT
-------------------------------------
An attestation the issuer has to vouch for is just an API call. This script never
sends the attestation anywhere - it re-derives the digest from the bytes we
posted, rebuilds the canonical payload, and checks an Ed25519 signature against
the key from `/api/v1/identity`. The only thing fetched from the node is a public
key that could equally have come from anywhere.

Note what stays out of `obolus/`: nothing in this file is importable from the
wallet. Authen is reached through `fetch` like any other resource, and the moment
Obolus grows first-class Authen verbs it stops being a wallet (DESIGN.md section 7).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sys
from pathlib import Path

import httpx
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from obolus.config import load_config  # noqa: E402
from obolus.session import live_session, open_vault  # noqa: E402
from obolus.x402 import fetch  # noqa: E402

BASE = "http://127.0.0.1:8402"
NOTARIZE = f"{BASE}/api/v1/notarize"
BODY = b"Obolus Phase 2 - offline verification probe"


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def canonical(payload: dict) -> bytes:
    """Sorted-keys compact JSON, UTF-8. Must match the signer byte for byte."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


async def main() -> int:
    cfg = load_config()
    if cfg.network.is_mainnet:
        raise SystemExit("Probe is testnet only.")
    session, record, ledger = live_session(cfg)
    vault, _ = open_vault(cfg)

    result = await fetch(
        cfg, session, NOTARIZE, method="POST", body=BODY,
        our_addresses={vault.address},
    )
    if not result.paid:
        raise SystemExit(f"Expected a challenge, got {result.status_code} unpaid.")
    ledger.record_spend(record, result.price_micro)

    print(f"paid    {cfg.network.fmt(result.price_micro)} from session "
          f"{record.index}")
    print(f"txid    {result.txid}")
    print(f"settled {result.receipt.get('success')}\n")

    doc = result.json()
    attestation = doc["attestation"]
    sig_b64, payload_b64 = attestation.split(".", 1)
    signature = b64url_decode(sig_b64)
    payload_bytes = b64url_decode(payload_b64)
    payload = json.loads(payload_bytes)

    checks: list[tuple[str, bool, str]] = []

    # 1. The digest must be of the bytes WE sent, not of whatever the node chose.
    ours = hashlib.sha256(BODY).hexdigest()
    checks.append(("digest matches the bytes we posted", payload["h"] == ours, ours))

    # 2. Canonical form. A payload that re-serialises differently would let two
    #    different documents share one signature.
    checks.append(
        ("payload is canonical", canonical(payload) == payload_bytes, "sorted, compact")
    )

    # 3. The signature, against the key the node publishes.
    identity = httpx.get(f"{BASE}/api/v1/identity", timeout=20).json()
    published = identity["publicKey"]
    checks.append(("payload names the published key", payload["k"] == published, published[:16] + "..."))

    try:
        VerifyKey(bytes.fromhex(published)).verify(payload_bytes, signature)
        signature_ok, note = True, "ed25519 ok"
    except BadSignatureError:
        signature_ok, note = False, "BAD SIGNATURE"
    checks.append(("signature verifies offline", signature_ok, note))

    # 4. A tampered payload must fail. A verifier that cannot fail proves nothing.
    tampered = dict(payload)
    tampered["h"] = "0" * 64
    try:
        VerifyKey(bytes.fromhex(published)).verify(canonical(tampered), signature)
        rejects_tampering = False
    except BadSignatureError:
        rejects_tampering = True
    checks.append(("tampered payload is rejected", rejects_tampering, "negative control"))

    for label, ok, note in checks:
        print(f"  [{'OK ' if ok else 'FAIL'}] {label:38} {note}")

    failed = [c for c in checks if not c[1]]
    print()
    print("VERDICT:", "attestation verifies offline" if not failed else "FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
