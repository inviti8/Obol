"""The CLI - how every phase is proved before an MCP client is in the loop.

Not a throwaway. It keeps the wallet testable without MCP, and it stays useful for
support afterwards: when someone's agent cannot pay, this is what tells them
whether the vault is on bootstrap step 1 or the session never closed.

    obol vault                     where the vault is, and what to do next
    obol vault optin               step 2 of the bootstrap
    obol session open --balance 1  open a funded session
    obol session close             close it and sweep back
    obol sessions                  what the ledger believes
    obol reap                      sweep anything a crash left behind

Every command takes `--network`, defaulting to testnet.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import PROFILES, VAULT_MIN_ALGO_MICRO, Config, load_config
from .errors import ObolError
from .ledger import Ledger
from .session import (
    close_session,
    live_session,
    open_session,
    open_vault,
    reap,
    vault_status,
    vault_optin,
)


def _explorer(cfg: Config, txid: str) -> str:
    host = "explorer" if cfg.network.is_mainnet else "testnet.explorer"
    return f"https://{host}.perawallet.app/tx/{txid}"


def cmd_vault(cfg: Config, args) -> int:
    st = vault_status(cfg)
    if st.created:
        print("Generated a new vault key.\n")
    print(f"network   {cfg.network.name}  asset {cfg.network.payment_asa}")
    print(f"vault     {st.address}")
    print(f"ALGO      {st.algo_micro / 1e6:.6f}")
    print(f"asset     {cfg.network.fmt(st.asset_micro)}  opted-in={st.opted_in}")
    print()
    if st.ready:
        print("READY - " + st.message)
    else:
        print(f"BOOTSTRAP STEP {st.step} of 3")
        print(f"  {st.message}")
    return 0


def cmd_vault_optin(cfg: Config, args) -> int:
    txid = vault_optin(cfg)
    print(f"Opted the vault into ASA {cfg.network.payment_asa}.")
    print(f"  txid      {txid}")
    print(f"  explorer  {_explorer(cfg, txid)}")
    return 0


def cmd_session_open(cfg: Config, args) -> int:
    balance = (
        cfg.network.to_units(args.balance)
        if args.balance is not None
        else cfg.caps.session_balance_micro
    )
    record, txid = open_session(cfg, balance)
    print(f"Session {record.index} open.")
    print(f"  address   {record.address}")
    print(f"  balance   {cfg.network.fmt(record.balance_micro)}")
    print(f"  funded    {record.funding_micro / 1e6:.6f} ALGO")
    print(f"  txid      {txid}")
    print(f"  explorer  {_explorer(cfg, txid)}")
    return 0


def cmd_session_close(cfg: Config, args) -> int:
    record, txid = close_session(cfg, args.index)
    if txid is None:
        print(f"Session {record.index} had no account on chain; marked closed.")
        return 0
    print(f"Session {record.index} closed, everything swept to the vault.")
    print(f"  txid      {txid}")
    print(f"  explorer  {_explorer(cfg, txid)}")
    return 0


def cmd_sessions(cfg: Config, args) -> int:
    ledger = Ledger.load(cfg.ledger_path)
    if not ledger.sessions:
        print("No sessions recorded.")
        return 0
    print(f"{'idx':>4}  {'state':<8} {'balance':>12}  address")
    for s in ledger.sessions:
        print(
            f"{s.index:>4}  {s.state:<8} "
            f"{cfg.network.fmt(s.balance_micro):>12}  {s.address}"
        )
    print(f"\nspent today: {cfg.network.fmt(ledger.spent_today())}")
    return 0


def cmd_reap(cfg: Config, args) -> int:
    results = reap(cfg)
    if not results:
        print("Nothing to reap - no live sessions in the ledger.")
        return 0
    failed = 0
    for record, txid, status in results:
        print(f"session {record.index}  {record.address}  {status}")
        if txid:
            print(f"    {_explorer(cfg, txid)}")
        if status.startswith("FAILED"):
            failed += 1
    return 1 if failed else 0


def cmd_fetch(cfg: Config, args) -> int:
    """Fetch a URL, paying from the open session if it challenges."""
    import asyncio

    from .caps import SpendContext
    from .errors import CapExceeded, PaymentRefused, PaymentRejected
    from .x402 import fetch

    session, record, ledger = live_session(cfg)
    vault, _ = open_vault(cfg)

    # What the ledger knows, handed to the caps. Session remaining is tracked
    # against what we have spent from it, not read from chain - the chain is the
    # real enforcement and this only makes the refusal legible.
    spend = SpendContext(
        spent_today_micro=ledger.spent_today(),
        session_remaining_micro=max(0, record.balance_micro - record.spent_micro),
    )

    body: bytes | None = None
    if args.body_file:
        body = Path(args.body_file).read_bytes()
    elif args.body is not None:
        body = args.body.encode()

    max_price = (
        cfg.network.to_units(args.max_price) if args.max_price is not None else None
    )

    try:
        result = asyncio.run(
            fetch(
                cfg,
                session,
                args.url,
                method=args.method,
                body=body,
                max_price_micro=max_price,
                our_addresses={vault.address},
                spend=spend,
            )
        )
    except CapExceeded as exc:
        # Named separately from a plain refusal: this one the user can raise.
        print(f"CAP: {exc.limit}\n{exc}", file=sys.stderr)
        return 4
    except PaymentRefused as exc:
        print(f"REFUSED\n{exc}", file=sys.stderr)
        return 2
    except PaymentRejected as exc:
        print(f"REJECTED\n{exc}", file=sys.stderr)
        return 3

    if result.paid:
        ledger.record_spend(record, result.price_micro)
        print(f"paid      {cfg.network.fmt(result.price_micro)} to {result.pay_to}")
        print(f"from      session {record.index} {result.payer}")
        print(f"txid      {result.txid}")
        print(f"explorer  {_explorer(cfg, result.txid)}")
        print(f"settled   {result.receipt.get('success')}")
    else:
        print(f"unpaid    {result.status_code} (no challenge)")
    print(f"type      {result.content_type}  {len(result.content)} bytes")
    print()

    if args.output:
        Path(args.output).write_bytes(result.content)
        print(f"body written to {args.output}")
    else:
        try:
            print(json.dumps(result.json(), indent=2)[: args.max_body])
        except Exception:
            print(result.content[: args.max_body].decode("utf-8", "replace"))
    return 0


def cmd_vault_qr(cfg: Config, args) -> int:
    """Show scannable funding URIs for the vault, as QR codes in the terminal.

    Two codes, not one. ALGO and the payment asset are different assets sent in a
    forced order (DESIGN.md section 3.1), and a single QR cannot say "this one
    first".
    """
    from .funding import default_logo, funding_targets, qr_styled_png, qr_terminal

    vault, _ = open_vault(cfg)
    targets = funding_targets(
        vault.address,
        cfg.network.payment_asa,
        network=cfg.network.name,
        algo_needed_micro=VAULT_MIN_ALGO_MICRO,
        asset_label=cfg.network.asset_label,
    )
    if args.asset_only:
        targets = [t for t in targets if t.what != "ALGO"]
    elif args.algo_only:
        targets = [t for t in targets if t.what == "ALGO"]

    print(f"network   {cfg.network.name}")
    print(f"vault     {vault.address}")
    if cfg.network.is_mainnet:
        print()
        print("*** MAINNET - anything sent here is real money ***")
    for target in targets:
        print()
        print(f"--- send {target.what} " + "-" * max(0, 44 - len(target.what)))
        print(f"  {target.why}")
        if target.suggested:
            print(f"  suggested: {target.suggested}")
        print(f"  {target.uri}")
        print()
        print(qr_terminal(target.uri))
        if args.write:
            out = Path(args.write)
            out.mkdir(parents=True, exist_ok=True)
            name = target.theme.key
            path = out / f"obol-fund-{cfg.network.name}-{name}.png"
            path.write_bytes(
                qr_styled_png(
                    target.uri,
                    logo=default_logo(),
                    dark=target.theme.modules,
                    light=target.theme.background,
                    caption=target.theme.label,
                )
            )
            print(f"  written to {path}")

    if args.open_page:
        from .view import ViewCard, open_in_browser, render_page, write_page

        st = vault_status(cfg)
        cards = [
            ViewCard(
                label=t.theme.label,
                uri=t.uri,
                png=qr_styled_png(
                    t.uri,
                    logo=default_logo(),
                    dark=t.theme.modules,
                    light=t.theme.background,
                    caption=t.theme.label,
                ),
                why=t.why,
                background=t.theme.background,
                suggested=t.suggested,
            )
            for t in targets
        ]
        page = write_page(
            render_page(
                cards,
                network=cfg.network.name,
                address=vault.address,
                balances={
                    "ALGO": f"{st.algo_micro / 1e6:.6f}",
                    cfg.network.asset_label: cfg.network.fmt(st.asset_micro),
                },
            ),
            network=cfg.network.name,
            tag=targets[0].theme.key if len(targets) == 1 else "all",
        )
        print()
        print(f"  page      {page}")
        print(f"  opened    {open_in_browser(page)}")

    print()
    print("No amount is encoded in either code - type it into your wallet, where")
    print("you can see it before confirming.")
    return 0


def cmd_address(cfg: Config, args) -> int:
    """Just the address, for piping into a faucet or a funding script."""
    vault, _ = open_vault(cfg)
    print(vault.address)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="obolus", description=__doc__)
    ap.add_argument(
        "--network",
        choices=sorted(PROFILES),
        default=None,
        help="default: testnet, or OBOL_NETWORK",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    vault = sub.add_parser("vault", help="vault status and bootstrap")
    vault_sub = vault.add_subparsers(dest="vault_command")
    vault.set_defaults(fn=cmd_vault)
    vault_sub.add_parser("optin", help="opt the vault into the payment asset").set_defaults(
        fn=cmd_vault_optin
    )
    vault_sub.add_parser("address", help="print the vault address only").set_defaults(
        fn=cmd_address
    )
    v_qr = vault_sub.add_parser("qr", help="scannable funding QR codes")
    v_qr.add_argument("--algo-only", action="store_true", help="just the ALGO code")
    v_qr.add_argument("--asset-only", action="store_true", help="just the asset code")
    v_qr.add_argument("--write", default=None, metavar="DIR", help="also save PNGs")
    v_qr.add_argument("--open", action="store_true", dest="open_page",
                      help="render a page and open it in a browser")
    v_qr.set_defaults(fn=cmd_vault_qr)

    session = sub.add_parser("session", help="session lifecycle")
    session_sub = session.add_subparsers(dest="session_command", required=True)
    s_open = session_sub.add_parser("open", help="open a funded session")
    s_open.add_argument("--balance", type=float, default=None, help="in whole units")
    s_open.set_defaults(fn=cmd_session_open)
    s_close = session_sub.add_parser("close", help="close and sweep back")
    s_close.add_argument("--index", type=int, default=None)
    s_close.set_defaults(fn=cmd_session_close)

    fetch = sub.add_parser("fetch", help="fetch a URL, paying if it challenges")
    fetch.add_argument("url")
    fetch.add_argument("--method", default="GET")
    fetch.add_argument("--body", default=None, help="request body as text")
    fetch.add_argument("--body-file", default=None, help="request body from a file")
    fetch.add_argument(
        "--max-price", type=float, default=None, help="refuse above this, in whole units"
    )
    fetch.add_argument("--output", default=None, help="write the body to a file")
    fetch.add_argument("--max-body", type=int, default=2000)
    fetch.set_defaults(fn=cmd_fetch)

    sub.add_parser("sessions", help="what the ledger believes").set_defaults(
        fn=cmd_sessions
    )
    sub.add_parser("reap", help="sweep orphaned sessions").set_defaults(fn=cmd_reap)
    return ap


def main() -> int:
    args = build_parser().parse_args()
    cfg = load_config(network=args.network)
    if cfg.network.is_mainnet:
        # Not a confirmation prompt - those belong on the spend path in Phase 3.
        # This is here so mainnet is never entered without noticing.
        print("*** MAINNET - real money ***\n", file=sys.stderr)
    try:
        return args.fn(cfg, args)
    except ObolError as exc:
        # The library raises these instead of SystemExit so a server can survive
        # them; the CLI is where they become an exit code.
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
