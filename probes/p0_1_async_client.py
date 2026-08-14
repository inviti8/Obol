"""P0.1 — can the x402 client complete a payment inside an asyncio event loop?

    uv run python probes/p0_1_async_client.py

The question `IMPLEMENTATION_PLAN.md` §0.1 says gates everything. MCP servers are
async; the Authen work drove `x402ClientSync` twice from ordinary blocking code.
If the client cannot be driven from a running loop, every call site changes.

WHAT THIS MEASURES, AND WHY IT IS NOT JUST "DOES IT WORK"
--------------------------------------------------------
Reading the SDK first (x402-avm 2.0.2) narrows the question considerably:

  * `x402.client` exports a native async `x402Client` whose
    `create_payment_payload` IS a coroutine.
  * Both clients share one generator, `_create_payment_payload_v2_core`. The
    async client awaits *hooks* only — at the scheme call it does a plain
    synchronous `client.create_payment_payload(selected)`.
  * `ExactAvmClientScheme.create_payment_payload` is not async, and calls
    `algod.suggested_params()` — a blocking HTTP round trip — inside itself.

So "does it work" is the easy half and the answer is almost certainly yes. The
half that decides the architecture is **whether it stalls the event loop**, since
a blocked loop in an MCP server means the whole server stops answering, not just
this call. That is what the heartbeat below is for: it ticks every 10 ms and
records the worst gap. A healthy loop shows single-digit milliseconds; a blocked
one shows the algod round trip.

Both variants pay for real, on testnet, against a loopback Authen node.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from algosdk import encoding, mnemonic

AUTHEN_ROOT = Path(os.environ.get("AUTHEN_ROOT", r"D:/repos/Authen"))
AUTHEN_PY = AUTHEN_ROOT / ".venv" / "Scripts" / "python.exe"
if not AUTHEN_PY.exists():  # POSIX layout
    AUTHEN_PY = AUTHEN_ROOT / ".venv" / "bin" / "python"
ACCOUNTS = AUTHEN_ROOT / ".venv" / "testnet_accounts.json"

HOST, PORT = "127.0.0.1", int(os.environ.get("OBOL_PROBE_PORT", "8402"))
BASE = f"http://{HOST}:{PORT}"
NOTARIZE = f"{BASE}/api/v1/notarize"
ALGOD_TESTNET = "https://testnet-api.algonode.cloud"
CAIP2_TESTNET = "algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI="

# A probe pays real (testnet) money to whatever it is pointed at. Loopback only:
# there is no configuration of this file that spends on mainnet.
assert HOST == "127.0.0.1", "probes pay; loopback only"


class BuyerSigner:
    """x402's ClientAvmSigner over a raw algosdk key.

    Ported verbatim from `D:/repos/Authen/tools/pay_once.py`. Signs only the group
    indexes it is asked to sign — the fee-payer transaction belongs to the
    facilitator and must be left untouched.

    Note it stays SYNCHRONOUS even under the async client: the AVM scheme calls
    `sign_transactions` directly, not through an await. That is load-bearing for
    `obolus/signer.py`, which therefore needs no async variant.
    """

    def __init__(self, sk: str, addr: str) -> None:
        self._sk = sk
        self._addr = addr

    @property
    def address(self) -> str:
        return self._addr

    def sign_transactions(
        self, unsigned_txns: list[bytes], indexes_to_sign: list[int]
    ) -> list[bytes | None]:
        out: list[bytes | None] = [None] * len(unsigned_txns)
        for i in indexes_to_sign:
            txn = encoding.msgpack_decode(base64.b64encode(unsigned_txns[i]).decode())
            out[i] = base64.b64decode(encoding.msgpack_encode(txn.sign(self._sk)))
        return out


class Heartbeat:
    """Ticks on the event loop and records the worst gap between ticks.

    This is the actual instrument. Wall-clock duration of a payment tells us
    nothing about whether the server could have served anyone else meanwhile;
    the largest stall does.
    """

    def __init__(self, interval: float = 0.01) -> None:
        self.interval = interval
        self.worst = 0.0
        self.ticks = 0
        self._stop = False

    async def run(self) -> None:
        last = time.perf_counter()
        while not self._stop:
            await asyncio.sleep(self.interval)
            now = time.perf_counter()
            gap = now - last - self.interval
            self.worst = max(self.worst, gap)
            self.ticks += 1
            last = now

    def stop(self) -> None:
        self._stop = True


def _decode_header(value: str) -> dict:
    return json.loads(base64.b64decode(value))


def _load_buyer() -> tuple[str, str]:
    if not ACCOUNTS.exists():
        raise SystemExit(
            f"No testnet accounts at {ACCOUNTS}.\n"
            "Run `python tools/testnet_setup.py --new` in the Authen repo."
        )
    buyer = json.loads(ACCOUNTS.read_text())["buyer"]
    return mnemonic.to_private_key(buyer["mnemonic"]), buyer["address"]


def start_node() -> tuple[subprocess.Popen, dict]:
    """Boot the Authen node under its own interpreter; return once it is READY."""
    if not AUTHEN_PY.exists():
        raise SystemExit(f"No Authen interpreter at {AUTHEN_PY}")
    proc = subprocess.Popen(
        [str(AUTHEN_PY), str(Path(__file__).parent / "_authen_node.py")],
        cwd=str(AUTHEN_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        line = proc.stdout.readline() if proc.stdout else ""
        if not line:
            if proc.poll() is not None:
                raise SystemExit(f"Node exited early with {proc.returncode}")
            continue
        if line.startswith("READY"):
            fields = dict(
                part.split("=", 1) for part in line.split()[2:] if "=" in part
            )
            return proc, fields
        print(f"  node| {line.rstrip()}")
    proc.kill()
    raise SystemExit("Node never became READY")


async def pay_async(signer: BuyerSigner, body: bytes) -> dict:
    """Variant A — the native async client, driven directly on the loop."""
    from x402.client import x402Client
    from x402.mechanisms.avm.exact import register_exact_avm_client
    from x402.schemas.payments import PaymentRequired

    async with httpx.AsyncClient(timeout=90) as http:
        r = await http.post(NOTARIZE, content=body)
        if r.status_code != 402:
            raise SystemExit(f"Expected 402, got {r.status_code}")
        challenge = _decode_header(r.headers["PAYMENT-REQUIRED"])

        client = x402Client()
        register_exact_avm_client(
            client, signer=signer, networks=CAIP2_TESTNET, algod_url=ALGOD_TESTNET
        )
        t0 = time.perf_counter()
        payload = await client.create_payment_payload(
            PaymentRequired.model_validate(challenge)
        )
        sign_seconds = time.perf_counter() - t0

        header = base64.b64encode(
            payload.model_dump_json(by_alias=True, exclude_none=True).encode()
        ).decode()
        # PAYMENT-SIGNATURE. Not PAYMENT, not X-PAYMENT — see CLAUDE.md.
        r = await http.post(
            NOTARIZE, content=body, headers={"PAYMENT-SIGNATURE": header}
        )
        return _finish(r, sign_seconds)


async def pay_sync_in_thread(signer: BuyerSigner, body: bytes) -> dict:
    """Variant B — the proven sync client, offloaded with asyncio.to_thread."""
    from x402.client import x402ClientSync
    from x402.mechanisms.avm.exact import register_exact_avm_client
    from x402.schemas.payments import PaymentRequired

    async with httpx.AsyncClient(timeout=90) as http:
        r = await http.post(NOTARIZE, content=body)
        if r.status_code != 402:
            raise SystemExit(f"Expected 402, got {r.status_code}")
        challenge = _decode_header(r.headers["PAYMENT-REQUIRED"])

        def build() -> str:
            client = x402ClientSync()
            register_exact_avm_client(
                client, signer=signer, networks=CAIP2_TESTNET, algod_url=ALGOD_TESTNET
            )
            payload = client.create_payment_payload(
                PaymentRequired.model_validate(challenge)
            )
            return base64.b64encode(
                payload.model_dump_json(by_alias=True, exclude_none=True).encode()
            ).decode()

        t0 = time.perf_counter()
        header = await asyncio.to_thread(build)
        sign_seconds = time.perf_counter() - t0

        r = await http.post(
            NOTARIZE, content=body, headers={"PAYMENT-SIGNATURE": header}
        )
        return _finish(r, sign_seconds)


def _finish(r: httpx.Response, sign_seconds: float) -> dict:
    if r.status_code != 200:
        again = r.headers.get("PAYMENT-REQUIRED")
        if again:
            # The reason rides in the re-issued challenge, not the body.
            print("  rejection:", json.dumps(_decode_header(again))[:400])
        raise SystemExit(f"Payment rejected: {r.status_code} {r.text[:300]}")
    raw = r.headers.get("PAYMENT-RESPONSE") or r.headers.get("X-PAYMENT-RESPONSE")
    if not raw:
        raise SystemExit("Served without a settlement receipt")
    receipt = _decode_header(raw)
    return {
        "sign_seconds": sign_seconds,
        "success": receipt.get("success"),
        "txid": receipt.get("transaction") or receipt.get("txHash") or "",
        "attestation": (r.json() or {}).get("attestation", "")[:60],
    }


async def run_variant(name: str, coro_fn, signer: BuyerSigner) -> dict:
    hb = Heartbeat()
    task = asyncio.create_task(hb.run())
    t0 = time.perf_counter()
    try:
        result = await coro_fn(signer, f"obolus P0.1 probe: {name}".encode())
    finally:
        hb.stop()
        await task
    result["total_seconds"] = time.perf_counter() - t0
    result["worst_stall_ms"] = hb.worst * 1000
    result["ticks"] = hb.ticks
    return result


async def amain() -> int:
    sk, addr = _load_buyer()
    signer = BuyerSigner(sk, addr)

    proc, node = start_node()
    print(f"node      {BASE}  asset={node.get('asset')} price={node.get('price')}")
    print(f"buyer     {addr}")
    if node.get("payTo") == addr:
        raise SystemExit("Buyer is the payTo. That is a self-transfer, not a payment.")
    print()

    results: dict[str, dict] = {}
    try:
        for name, fn in (
            ("A: async client on the loop", pay_async),
            ("B: sync client in to_thread", pay_sync_in_thread),
        ):
            print(f"--- {name}")
            res = await run_variant(name, fn, signer)
            results[name] = res
            print(
                f"    settled={res['success']} total={res['total_seconds']:.2f}s "
                f"sign={res['sign_seconds']:.2f}s"
            )
            print(
                f"    worst event-loop stall {res['worst_stall_ms']:.1f} ms "
                f"over {res['ticks']} ticks"
            )
            print(f"    txid {res['txid']}")
            print()
    finally:
        proc.kill()

    a = results.get("A: async client on the loop", {})
    b = results.get("B: sync client in to_thread", {})
    print("VERDICT")
    print(f"  async client completes a payment from a running loop: "
          f"{'YES' if a.get('success') else 'NO'}")
    print(f"  it blocks the loop meanwhile:                        "
          f"{a.get('worst_stall_ms', 0):.0f} ms")
    print(f"  sync client in a thread keeps the loop responsive:   "
          f"{b.get('worst_stall_ms', 0):.0f} ms")
    return 0 if a.get("success") and b.get("success") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
