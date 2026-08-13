"""Serve the Authen node on loopback, for probes to pay against.

RUN THIS WITH AUTHEN'S INTERPRETER, not Obol's:

    D:/repos/Authen/.venv/Scripts/python.exe probes/_authen_node.py

Obol's venv deliberately carries client-side x402 extras only — it never serves a
402 — so it cannot import flask or the Authen package. Keeping the two processes
in separate interpreters is the point: the probe then exercises a real HTTP
merchant rather than an in-process fixture, which is what `x402_fetch` will face.

Config comes from `D:/repos/Authen/config/node.local.toml`, picked up
automatically by `load_config()` when it exists. That file is testnet, prices at
$0.05, pays to the testnet treasury, and — importantly — carries a LOCAL tag
rather than `x402-global-challenge`. The facilitator auto-catalogs whatever
`resourceUrl` it sees on /verify, permanently, so a loopback URL must never be
paid under the competition tag.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

AUTHEN_ROOT = Path(os.environ.get("AUTHEN_ROOT", r"D:/repos/Authen"))
HOST = "127.0.0.1"
PORT = int(os.environ.get("OBOL_PROBE_PORT", "8402"))

sys.path.insert(0, str(AUTHEN_ROOT))


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, *_args) -> None:  # noqa: ANN002
        pass


def main() -> int:
    from authen.config import load_config
    from authen.web.app import create_app

    cfg = load_config()
    if cfg.network.name != "testnet":
        raise SystemExit(
            f"Refusing to serve a probe node on {cfg.network.name}. "
            "Probes pay this node; only testnet is acceptable."
        )

    app = create_app(cfg)
    srv = make_server(HOST, PORT, app, handler_class=_QuietHandler)
    # The probe waits on this line, so it must be flushed, not buffered.
    print(
        f"READY http://{HOST}:{PORT} network={cfg.network.name} "
        f"asset={cfg.network.usdc_asa} price={cfg.price_micro_usdc} "
        f"payTo={cfg.pay_to}",
        flush=True,
    )
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
