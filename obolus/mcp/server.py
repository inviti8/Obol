"""The MCP server: wiring, lifecycle, and the stdio entry point.

    obolus-mcp                 # testnet, the default
    OBOL_NETWORK=mainnet obolus-mcp

Startup order matters and is deliberate:

1. **Reap first.** Whatever a previous run left behind is swept before any tool
   can answer. An unclean exit costs nothing as long as the next start happens.
2. **Do not open a session.** A server that funds an account when a client
   connects charges an agent that may never buy anything. The first paid call
   opens one (`Wallet.ensure_session`).
3. **Start the idle monitor.** MCP gives no reliable session-end signal on any
   transport, so a session that stops being used is closed on a timer.

Shutdown closes the session, but the reaper - not this handler - is what makes
the guarantee. A process that is killed never runs a shutdown handler at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer

from .. import __version__
from ..config import Config, load_config
from .tools import register
from .wallet import Wallet

log = logging.getLogger("obolus.mcp")

INSTRUCTIONS = """\
Obolus is a disposable Algorand wallet for paying x402 resources.

Use `x402_fetch` for any URL that might require payment - it returns the body
whether or not payment was needed. Check `wallet_status` if a payment fails or
before assuming funds exist. `wallet_funding_info` is for the human; the agent
cannot fund the wallet itself.

Payments come from a short-lived session account, so the loss ceiling for the
whole session is its balance. A settled payment proves settlement and nothing
else - not that the resource was correct or worth its price."""


def build_server(cfg: Config) -> MCPServer:
    wallet = Wallet(cfg)

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[Wallet]:
        for line in await wallet.startup():
            log.info("reaped %s", line)
        monitor = asyncio.create_task(wallet.idle_monitor())
        try:
            yield wallet
        finally:
            monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor
            await wallet.shutdown()

    server = MCPServer(
        name="obolus",
        title="Obolus - x402 wallet",
        # From the package, not a literal. This is what every connected client
        # sees in the handshake and quotes in a bug report; hardcoded, it went
        # on saying 0.1.0 through two releases.
        version=__version__,
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
    )
    register(server, wallet)
    return server


def serve(cfg: Config) -> int:
    """Run the stdio server against an already-resolved config.

    Split out from `main` so `obolus mcp` and the `obolus-mcp` script are the
    same code path rather than two that drift. The CLI route matters because it
    is what `uvx obolus mcp` reaches - see the note on `cmd_mcp` in cli.py.
    """
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,  # stdout is the MCP transport; anything on it corrupts the protocol
        format="%(levelname)s %(name)s: %(message)s",
    )
    if cfg.network.is_mainnet:
        log.warning("MAINNET - x402_fetch will spend real money")
    log.info(
        "obolus mcp %s: network=%s asset=%s data=%s",
        __version__,
        cfg.network.name,
        cfg.network.payment_asa,
        cfg.data_dir,
    )
    build_server(cfg).run("stdio")
    return 0


def main() -> int:
    return serve(load_config())


if __name__ == "__main__":
    sys.exit(main())
