# Obolus — Claude Code plugin

Wires up the [Obolus](https://github.com/inviti8/Obolus) MCP server and ships the
one piece of knowledge the server itself cannot carry: how to get a wallet from
empty to able to pay.

## Install

```
/plugin marketplace add inviti8/Obolus
/plugin install obolus@heavymeta
```

**Requires [uv](https://docs.astral.sh/uv/) on PATH.** The server is launched with
`uvx obolus mcp`, which fetches the package on first run — there is nothing to
install beforehand and nothing left behind.

## What you get

| | |
|---|---|
| `x402_fetch` | Fetch a URL, paying automatically if it answers `402`. |
| `wallet_status` | Whether the wallet can pay, and what it has spent today. |
| `wallet_funding_info` | The address, and which of the three bootstrap steps you are on. |
| `/obolus:fund-wallet` | The bootstrap, the forced order, and how to tell a funding failure from a cap refusal. |

## It starts on testnet

`.mcp.json` sets `OBOLUS_NETWORK=testnet` explicitly rather than relying on the
default, so the safe setting is visible to anyone reading the plugin rather than
implied. Mainnet is never disabled — Obolus exists to make real payments — but it
should not be reached by forgetting to choose.

To move to mainnet, set `OBOLUS_NETWORK=mainnet` in your own MCP configuration.
The two networks keep separate ledgers.

## Where the keys live

Nowhere near this directory. `OBOLUS_DATA_DIR` is deliberately **not** set here, so
the vault seed goes to the per-user default — `%LOCALAPPDATA%\Obolus` on Windows,
`~/.obolus` elsewhere.

Do not point it inside the plugin directory. Plugin directories are replaced on
update, and the seed is the only way back to money already on chain. A wallet
whose seed was overwritten by a routine plugin update presents as a working
install with an empty balance.

## Versioning

The plugin version tracks the PyPI package version, and `tests/test_packaging.py`
in the main repo fails the build if they drift. Note that `version` is set in
`plugin.json` only, never in the marketplace entry — Claude Code takes the
`plugin.json` value without warning when both are present, so a second copy is a
silent trap rather than a redundancy.

## Licence

Apache-2.0, same as Obolus.
