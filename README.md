<!-- mcp-name: io.github.inviti8/obolus -->

<p align="center">
  <img src="https://raw.githubusercontent.com/inviti8/Obolus/main/assets/obolus-logo.svg" alt="Obolus" width="120" height="120">
</p>

<h1 align="center">Obolus</h1>

<!-- The logo above is a placeholder. Replace assets/obolus-logo.svg; the only
     reference is the absolute URL above, which is absolute because PyPI does
     not resolve relative paths in a long description. -->

<p align="center">
An <b>MCP server that gives an AI agent a disposable Algorand wallet</b>, so it can
pay for <a href="https://x402.org">x402</a> resources without a human provisioning
anything.
</p>

The name is the coin paid to the ferryman for passage: small denomination, one
purpose, spent and gone.

```
agent → x402_fetch(url) → 402 → sign → pay → body
```

x402 has over a thousand listed resources and almost no buyers — a handful of
wallets account for nearly all volume, and they are scripted loops rather than
agents. The rail exists; nothing can reach it. Obolus is the buyer-side piece:
account setup, opt-in, signing and sweeping handled invisibly, so an agent just
spends.

---

## Install

```bash
uv tool install obolus
```

`uv tool install` rather than `uvx`: `uvx` re-resolves dependencies on every
invocation, which is the wrong trade for something an agent calls constantly.
`pipx install obolus` works too.

### Add it to Claude Code

```bash
claude mcp add obolus -e OBOLUS_NETWORK=testnet -- obolus-mcp
```

### Add it to any other MCP client

```json
{
  "mcpServers": {
    "obolus": {
      "command": "obolus-mcp",
      "env": { "OBOLUS_NETWORK": "testnet" }
    }
  }
}
```

**It starts on testnet.** Mainnet is never disabled — Obolus is meant to make real
payments — but it is never what you get by forgetting to choose.

---

## Fund it

A new vault holds nothing, and the order is forced by the chain:

```bash
obol vault          # says which of the three steps you are on
obol vault qr       # scannable codes for the two things you can send
```

| # | Step | Who |
|---|---|---|
| 1 | Send ≥ 0.21 ALGO to the vault | you |
| 2 | Opt the vault into USDC (`obol vault optin`) | Obolus |
| 3 | Send USDC | you |

**Step 3 before step 2 fails.** An Algorand account cannot receive an asset it
has not opted into — the transfer is rejected outright, it does not sit pending.
That is why the QR codes encode the asset id and not just the address, and why
`wallet_funding_info` tells you which step you are on every time rather than only
on error.

The QR codes carry **no amount**. You type that into your own wallet, where you
see it before confirming.

---

## Use it

Three tools, deliberately:

| Tool | What it does |
|---|---|
| `x402_fetch` | Fetch a URL, paying if it answers `402`. Returns the body. |
| `wallet_status` | Balances, the active session, spending today, the caps. |
| `wallet_funding_info` | How to put money in. For the human, not the agent. |

There is no tool for any particular merchant. Everything is reached through
`x402_fetch`, because the moment a wallet grows first-class verbs for one seller
it stops being a wallet.

---

## What bounds a loss

**The session balance, enforced by the chain.** Payments come from a short-lived
session account funded from your vault, never from the vault itself. A session
cannot spend what it does not hold, and that is true regardless of what any code
here does.

Everything else is in-process and configurable in `config.toml` inside your data
directory:

```toml
[caps]
session_balance_micro = 5000000   # $5 per session
per_call_micro        = 500000    # $0.50 per call
daily_micro           = 2000000   # $2 a day, resets 00:00 UTC
allowlist             = []        # payTo addresses or hostnames; empty = any

[session]
idle_timeout_seconds  = 600       # close and sweep back after this

[files]
# root = "/path/you/choose"       # off unless set - see Safety
```

Some refusals are not caps and have no config key: paying an address we control,
a non-`https` resource on mainnet, an asset the wallet does not hold, and any
challenge that is not a well-formed x402 v2 `402`.

---

## Safety

**Your seed is the only way back to your money.** It lives in your data
directory. Pointing `OBOLUS_DATA_DIR` at a new location creates a *new* wallet; it
does not move the old one. Back the directory up.

**File access is off by default.** `x402_fetch` can send a file to a paid
endpoint and write the response back, which is how you get an image signed or a
document processed. Both are disabled unless you set `[files] root`, and confined
to that directory when you do. This is deliberate: moving bytes off your machine
is not something a spend cap can bound.

**Approval is per tool, not per payment.** Most MCP clients ask once and remember.
That means the caps above are your real spending boundary, not the prompt — see
[`DESIGN.md`](https://github.com/inviti8/Obolus/blob/main/DESIGN.md) §7.1, which documents what was measured rather than
what was assumed.

**A settled payment proves settlement and nothing else** — not that the resource
was correct, honest, or worth its price.

---

## Documentation

- **[CLAUDE.md](https://github.com/inviti8/Obolus/blob/main/CLAUDE.md)** — orientation, and the x402 facts that cost real
  debugging time to learn.
- **[DESIGN.md](https://github.com/inviti8/Obolus/blob/main/DESIGN.md)** — architecture, the security model, and the
  limitations stated plainly.
- **[IMPLEMENTATION_PLAN.md](https://github.com/inviti8/Obolus/blob/main/IMPLEMENTATION_PLAN.md)** — build order and what
  each phase actually proved.

## Status

Working on testnet: payments settle, sessions open and sweep themselves, and an
unclean exit is recovered on the next start. The first mainnet payment is the
next milestone.
