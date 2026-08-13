# Obol — implementation plan

Build order for the design in [`DESIGN.md`](./DESIGN.md). Read
[`CLAUDE.md`](./CLAUDE.md) first for the x402 facts that are expensive to
rediscover.

**Written:** 2026-08-12. **Status:** nothing built.

> **Path note.** The Authen repo is on GitHub as `inviti8/Authen` but its local
> directory is still **`D:/repos/PintheonV2`**. Every reference below uses the
> local path. `D:/repos/Authen` does not exist.

---

## 0. Before writing any module code

Two unknowns change the shape of the code, not just its details. Resolve both
first — each is hours, not days, and discovering either late means rework.

### P0.1 — Can the x402 client run inside an async MCP server?

**Why it gates everything.** The Authen work drove `x402ClientSync` end to end
twice. MCP servers are async. If the sync client cannot be called from an event
loop, every call site changes.

`x402.client` exports both `x402Client` and `x402ClientSync`. Check whether
`register_exact_avm_client` works against the async variant, or whether the sync
client must be offloaded to a thread.

**Deliverable:** one script that completes a testnet payment from inside a
running asyncio loop. Record which variant worked in this file.

### P0.2 — Does the facilitator accept a LogicSig envelope?

Per `DESIGN.md` §6, the client passes signer bytes through opaquely (verified),
so this is purely a facilitator question. Sign an `exact` payment with a LogicSig
instead of a raw key, submit to testnet, observe.

**The answer does not block v1** — §6 recommends deferring the policy build
regardless. It is worth knowing because it is permanent and cheap.

### Decisions still open

- **MCP session boundary.** No transport gives a reliable session-end signal.
  Working assumption: idle timeout plus reaping on next start. Revisit once a
  real client is in the loop (Phase 4).
- **Testnet USDC.** The Authen work minted a stand-in ASA when the USDC faucet
  was rate-limited (`D:/repos/PintheonV2/tools/testnet_setup.py`). Same fallback
  applies; the `exact` scheme takes any ASA id.

---

## 1. Module layout

```
obol/
  config.py        settings, network profiles, caps, data dir
  keys.py          vault keypair, OS keychain, address encodings
  algorand.py      algod client, account state, group builders
  session.py       session lifecycle: open, fund, opt in, close, sweep
  ledger.py        session registry (for reaping), spend counters
  signer.py        ClientAvmSigner implementation
  x402.py          the 402 flow: challenge, sign, replay, receipt
  caps.py          per-call, daily, allowlist enforcement
  cli.py           drives everything without MCP — the test harness
  mcp/
    server.py      MCP server wiring
    tools.py       x402_fetch, wallet_status, wallet_funding_info
tests/
```

**`cli.py` is not a throwaway.** Every phase below is provable from the CLI
before MCP exists. It keeps the wallet testable without an MCP client in the
loop, and it stays useful for support and debugging afterwards.

---

## 2. What to port, not rewrite

| From `D:/repos/PintheonV2` | Into | Notes |
|---|---|---|
| `tools/pay_once.py` → `BuyerSigner` | `obol/signer.py` | Near-direct port. Signs only requested group indexes; the fee-payer txn belongs to the facilitator. |
| `tools/pay_once.py` → the 402 flow | `obol/x402.py` | Challenge decode, `create_payment_payload`, **`PAYMENT-SIGNATURE`** header, receipt decode. |
| `tools/algo.py` | `obol/algorand.py` | `account_info`, `asset_holding`, formatting, ALGOD urls. |
| `authen/keys.py` | `obol/keys.py` | Ed25519 identity, Stellar/Algorand address encoding, **atomic write with `O_BINARY`** — see the bug note below. |
| `tools/pay_mainnet.py` | `obol/cli.py` | The preflight/guard pattern: check everything before spending, refuse without explicit confirmation. |

**Carry the `O_BINARY` lesson.** `authen/keys.py` writes its seed with
`os.open(..., os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0))`.
Without `O_BINARY`, Windows text mode expands any `0x0A` in a 32-byte seed to
`0x0D 0x0A`; the file reads back 33 bytes and the wallet refuses to start. It
presents as an intermittent failure on roughly one install in eight.

---

## 3. Phases

Each phase ends with something demonstrable. Do not start the next until the
current one passes on testnet.

### Phase 1 — Wallet core (no x402, no MCP)

`config.py`, `keys.py`, `algorand.py`, `session.py`, `ledger.py`, `cli.py`.

- Vault keypair generated on first run, stored via `keyring`, address printed
- `session open` — one atomic group: fund ALGO → opt into USDC → transfer balance
- `session close` — one atomic group: `close_assets_to` then `close_remainder_to`
- `ledger` persists every session address **at creation**, before funding
- `reap` sweeps orphaned sessions found in the ledger

**Done when:** against testnet, `session open` then `session close` returns the
full balance to the vault, and — separately — killing the process between open
and close, then running `reap`, recovers it. Test that crash path explicitly;
it is the one that silently loses money in production.

### Phase 2 — x402 payment

`signer.py`, `x402.py`.

- Port `BuyerSigner`
- `fetch(url, method, body)` — challenge, sign, replay, return body + receipt
- Refuses non-`402` challenge shapes rather than guessing

**Done when:** `obol fetch https://<authen-testnet>/api/v1/notarize` pays and
returns a signed attestation, from a session account, with the receipt printed.

### Phase 3 — Caps and consent

`caps.py`.

- Per-call maximum, checked before signing
- Daily total, persisted, resets on UTC day boundary
- Optional payTo/host allowlist
- Session balance is the chain-enforced backstop and needs no code

**Done when:** each cap refuses correctly and the refusal names which limit was
hit. Unit-testable without network.

### Phase 4 — MCP server

`mcp/server.py`, `mcp/tools.py`.

- `x402_fetch`, `wallet_status`, `wallet_funding_info`
- Session opened lazily on first paid call, not at startup
- Tool descriptions carry the honest caveats from `DESIGN.md` §7

**Done when:** installed in a real MCP client, an agent completes a paid
notarization against Authen testnet without the human touching a key.

### Phase 5 — v1.1 funding paths

MoonPay embed (`DESIGN.md` §5) and the staking-funded credit ledger (§5.1).
Both are additive and neither blocks v1.

---

## 4. Test strategy

**Unit, no network:** address encodings against known vectors, cap enforcement,
canonical challenge parsing, ledger state machine.

**Integration, testnet, real transactions:** session open/close round trip,
balance reconciliation to the microunit, opt-in behaviour, the reaper.

**The crash test is mandatory and belongs in CI if CI touches testnet at all.**
Kill mid-session, assert the next `reap` recovers the exact expected amount.

**End to end:** MCP client → `x402_fetch` → Authen testnet → attestation, with
the returned attestation verified offline.

---

## 5. Risks, in order

1. **Async/sync mismatch (P0.1).** Highest-impact unknown. Probe first.
2. **Session boundary.** No clean end signal means balances sit in session
   accounts longer than intended. Mitigated by the reaper, not solved by it.
3. **Testnet USDC availability.** Faucets rate-limit. Fallback is a self-minted
   6-decimal ASA, already proven in the Authen work.
4. **MCP client variation.** Tool-approval behaviour differs between clients;
   the consent model in `DESIGN.md` §7 may need adjusting per client.
5. **Onramp minimums (v1.1).** Several providers sit at $5–15 minimum, which is
   large relative to a $5 session balance. Vault top-ups will be chunkier than
   session spends — design the funding UX around that, not against it.

---

## 6. Definition of done for v1

- A developer with USDC on Algorand installs Obol, runs one command, and their
  agent can pay for x402 resources within a budget they set once.
- No key ever leaves the machine.
- An unclean exit loses nothing.
- The wallet works against any x402 resource, not only Authen.
