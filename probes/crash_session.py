"""Open a session and die, on purpose, at a chosen instant.

    uv run python probes/crash_session.py --crash-at reserved
    uv run python probes/crash_session.py --crash-at submitted

The crash path is the one that silently loses money in production, so it gets
tested by actually crashing rather than by mocking a crash. `os._exit` skips
atexit handlers, buffer flushes and `finally` blocks — as close to a power cut as
a process can get to itself, and much harsher than an exception.

Two windows, and they fail differently:

  reserved   ledger written, group NOT submitted. The session address exists only
             on disk; no account was ever created. The reaper must recognise this
             and close the record without trying to sweep a phantom.

  submitted  group confirmed on chain, ledger still says "opening" because the
             process died before `mark_open`. This is the expensive one: 0.21 ALGO
             plus the whole session balance is sitting in an account nothing knows
             is finished. The reaper must find it from the index alone and recover
             every microunit.

After either, run `obol reap` and reconcile.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from obolus import algorand  # noqa: E402
from obolus.config import SESSION_FUNDING_MICRO, load_config  # noqa: E402
from obolus.keys import derive_session_key  # noqa: E402
from obolus.ledger import Ledger  # noqa: E402
from obolus.session import open_vault  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--crash-at", choices=("reserved", "submitted"), required=True)
    ap.add_argument("--balance", type=float, default=1.0)
    args = ap.parse_args()

    cfg = load_config()
    if cfg.network.is_mainnet:
        raise SystemExit("Crash probe is testnet only.")

    vault, _ = open_vault(cfg)
    ledger = Ledger.load(cfg.ledger_path)
    if ledger.live_sessions():
        raise SystemExit("A session is already live. Reap first.")

    balance = cfg.network.to_units(args.balance)
    session = derive_session_key(vault.seed, ledger.next_index)
    record = ledger.reserve_session(session.address, SESSION_FUNDING_MICRO, balance)
    print(f"reserved session {record.index} {record.address}", flush=True)

    if args.crash_at == "reserved":
        print("CRASHING before submit", flush=True)
        os._exit(9)

    cli = algorand.client(cfg.network)
    sp = algorand.suggested_params(cli)
    group = algorand.build_session_open_group(
        sp, vault, session, cfg.network.payment_asa, SESSION_FUNDING_MICRO, balance
    )
    txid = algorand.submit(cli, group)
    print(f"submitted and confirmed {txid}", flush=True)
    print("CRASHING before the ledger learns it succeeded", flush=True)
    os._exit(9)


if __name__ == "__main__":
    sys.exit(main())
