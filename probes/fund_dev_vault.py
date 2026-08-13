"""Fund an Obol dev vault from the Authen testnet buyer account.

    uv run python probes/fund_dev_vault.py --algo 0.5
    uv run python probes/fund_dev_vault.py --asset 2.0

DEV TOOLING, TESTNET ONLY. It signs with the throwaway buyer key from
`D:/repos/Authen/.venv/testnet_accounts.json` and refuses to touch mainnet. The
two flags are separate on purpose: ALGO must land before the vault can opt in,
and the asset transfer only succeeds after that opt-in — which is the three-step
bootstrap in DESIGN.md §3.1, walked by hand rather than described.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from algosdk import mnemonic, transaction

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from obol import algorand  # noqa: E402
from obol.config import load_config  # noqa: E402
from obol.session import open_vault  # noqa: E402

ACCOUNTS = Path(
    os.environ.get("AUTHEN_ROOT", r"D:/repos/Authen")
) / ".venv" / "testnet_accounts.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--algo", type=float, default=0.0, help="ALGO to send")
    ap.add_argument("--asset", type=float, default=0.0, help="payment asset to send")
    ap.add_argument("--from-account", default="buyer", choices=("buyer", "treasury"))
    args = ap.parse_args()

    cfg = load_config()
    if cfg.network.is_mainnet:
        raise SystemExit("Dev funding is testnet only. Refusing to run on mainnet.")

    src = json.loads(ACCOUNTS.read_text())[args.from_account]
    sk = mnemonic.to_private_key(src["mnemonic"])
    vault, _ = open_vault(cfg)

    cli = algorand.client(cfg.network)
    sp = algorand.suggested_params(cli)
    asa = cfg.network.payment_asa

    txns = []
    if args.algo:
        txns.append(
            transaction.PaymentTxn(
                sender=src["address"], sp=sp, receiver=vault.address,
                amt=int(round(args.algo * 1e6)),
            )
        )
    if args.asset:
        state = algorand.account_state(cli, vault.address, asa)
        if not state.opted_in:
            raise SystemExit(
                f"Vault is not opted into ASA {asa}; this transfer would be "
                "rejected outright. Run `obol vault optin` first — that is step 2."
            )
        txns.append(
            transaction.AssetTransferTxn(
                sender=src["address"], sp=sp, receiver=vault.address,
                amt=cfg.network.to_units(args.asset), index=asa,
            )
        )
    if not txns:
        raise SystemExit("Nothing to send. Pass --algo and/or --asset.")

    if len(txns) > 1:
        transaction.assign_group_id(txns)
    txid = algorand.submit(cli, [t.sign(sk) for t in txns])
    print(f"sent from {src['address']}")
    print(f"     to   {vault.address}")
    print(f"     txid {txid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
