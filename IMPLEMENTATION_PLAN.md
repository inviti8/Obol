# Obol — implementation plan

Build order for the design in [`DESIGN.md`](./DESIGN.md). Read
[`CLAUDE.md`](./CLAUDE.md) first for the x402 facts that are expensive to
rediscover.

**Written:** 2026-08-12. **Status:** nothing built; build decisions taken (§0).

> **Path note.** The Authen repo is `inviti8/Authen` on GitHub and
> **`D:/repos/Authen`** on disk. The rename from `PintheonV2` has happened and
> `D:/repos/PintheonV2` no longer exists — an earlier draft of this file asserted
> the reverse. Checked 2026-08-12.

---

## 0. Before writing any module code

Two unknowns change the shape of the code, not just its details. Resolve both
first — each is hours, not days, and discovering either late means rework.

**P0.1 is answered** (2026-08-12): sync client in a thread. P0.2 remains open and
blocks nothing.

### P0.1 — Can the x402 client run inside an async MCP server? — **ANSWERED**

**Answer: use `x402ClientSync` inside `asyncio.to_thread`.** Not the async
client, even though the async client works.

Probe: `probes/p0_1_async_client.py`, run 2026-08-12 against a loopback Authen
node on testnet. Both variants completed real settled payments; the difference is
not whether they work but what they do to the event loop.

| Variant | Settled | Payment took | **Worst event-loop stall** |
|---|---|---:|---:|
| A — native `x402Client`, awaited on the loop | yes | 7.55 s | **968 ms** |
| B — `x402ClientSync` in `asyncio.to_thread` | yes | 2.43 s | **22 ms** |

**Why the async client is the wrong answer despite working.** Reading
`x402-avm` 2.0.2: both clients share one generator,
`x402ClientBase._create_payment_payload_v2_core`. The async client awaits *hooks*
only — at the scheme it makes a plain synchronous call. And
`ExactAvmClientScheme.create_payment_payload` is not a coroutine: it calls
`algod.suggested_params()`, a blocking HTTP round trip, inline. So `await`ing the
async client parks the whole loop for the duration of an algod call. In an MCP
server that means the server stops answering *everything*, not just this request,
for about a second per payment. The heartbeat measured exactly that.

Variant B is also 3× faster wall-clock here, which is incidental — testnet
variance — and not the reason to choose it.

**Consequences for the build, all of them narrowing:**

- `obol/x402.py` exposes `async def fetch(...)` and does the payload build in
  `asyncio.to_thread`. The blocking work is quarantined in one function.
- **`BuyerSigner` stays synchronous.** The scheme calls `sign_transactions`
  directly, never through an await, so the port from `pay_once.py` needs no async
  variant. The Authen code carries over unchanged.
- The proven-twice sync path stays the code that touches money. That was the
  preferred outcome anyway; it is now the measured one.

**Two facts re-confirmed on the wire** while the probe ran, both already in
`CLAUDE.md` and both worth having seen again: the buyer's ALGO was unchanged
across two settlements (`1.998` before and after — the facilitator sponsors the
fee via `feePayer`), and only the asset balance moved, 96.8 → 96.7.

### P0.2 — Does the facilitator accept a LogicSig envelope?

Per `DESIGN.md` §6, the client passes signer bytes through opaquely (verified),
so this is purely a facilitator question. Sign an `exact` payment with a LogicSig
instead of a raw key, submit to testnet, observe.

**The answer does not block v1** — §6 recommends deferring the policy build
regardless. It is worth knowing because it is permanent and cheap.

### Decisions taken — 2026-08-12

| # | Decision | Consequence |
|---|---|---|
| 1 | **Run P0.1 before any module code.** | An hour, and the answer shapes every call site. P0.2 (LogicSig) stays cheap-and-optional. |
| 2 | **Vault key is a file on disk for v1**; `keyring` is for the finished product. | Port `authen/keys.py` verbatim, `O_BINARY` and all. Keychain is a later backend swap behind the same interface. |
| 3 | **All development runs on testnet rails.** Point at mainnet only for the demo and the first real payment. | No mainnet spend happens by accident, but mainnet is never blocked in code — see decision 4. |
| 4 | **Obol itself takes the first mainnet payment** against `https://authen.hvym.link`. | Mainnet is a supported target from day one, guarded like `pay_mainnet.py` rather than disabled. |
| 5 | **Closed source for now**; licence undecided. | No `LICENSE` file. Do not add public-repo furniture until the licence is chosen. |
| 6 | **One session per vault at a time** for v1, serialised. | Sidesteps nonce/ordering on vault-signed funding groups entirely. Revisit only if a real client needs concurrency. |

**On decision 4 — what the September 1 deadline actually requires.** Authen's
gate (`D:/repos/Authen/CLAUDE.md`) is **2026-09-01, 11:45pm EST**: one real
completed payment against a live mainnet endpoint. That endpoint is already up
and challenging correctly (verified below), so the gate is one payment away by
either route:

- **Preferred:** Obol's `x402_fetch` makes it. The wallet's first act is a real
  purchase from a real merchant — which is also the strongest possible demo.
- **Backstop:** `D:/repos/Authen/tools/pay_mainnet.py --pay --confirm`, which is
  finished and preflighted today.

The backstop exists so the deadline never depends on Obol shipping. **Do not let
Obol's schedule put the gate at risk** — if Phase 4 is not solid by roughly
2026-08-28, run the backstop and let Obol take the *second* payment.

### Verified state of the rails — 2026-08-12

Measured, not assumed. Re-check before relying on any of it.

**Authen mainnet is live.** `POST https://authen.hvym.link/api/v1/notarize`
returns `402` with a well-formed challenge:

| Field | Value |
|---|---|
| `asset` | `31566704` (real mainnet USDC) |
| `amount` | `50000` micro-USDC = $0.05 |
| `payTo` | `E64BQIOXKTT4BVMIFY2S5WX337FT6MLF66UPZEUPDYAKT4QIFOXXQCR24Q` |
| `extra.tag` | `x402-global-challenge` — correctly nested inside `accepts[]` |
| `extra.feePayer` | `ZMFK2OI7ZBD2U27ISERZC4S6LKM6WMFJPZQ4MYNJDZ2VNBNMBA67RA22AA` |

**Testnet does not use testnet USDC — and this changes the code.** Both Authen
testnet accounts are opted into real testnet USDC (`10458941`) and hold **zero**
of it. The balance that exists is a self-minted 6-decimal stand-in,
**ASA `769120200`**:

| Account | ALGO | ASA `769120200` |
|---|---:|---:|
| treasury `NJO3MQ…NNNFYI` | 7.995 | 999,903.2 |
| buyer `GSSX5NVB…UKXJTM` | 1.998 | 96.8 |

Two consequences, both load-bearing:

1. **Never hardcode a USDC asset id.** `DESIGN.md` §3 originally had the session
   opt into `31566704` at setup. On the only rail we can develop against, that
   opt-in is for the wrong asset and every payment fails. The payment asset is a
   **network-profile setting**, cross-checked against `accepts[].asset` in the
   challenge — see `DESIGN.md` §3.
2. **Testnet funding is solved and needs no faucet.** The Authen treasury can
   fund an Obol vault with both ALGO and the stand-in ASA. This retires risk #3.

### Decisions still open

- **MCP session boundary.** No transport gives a reliable session-end signal.
  Working assumption: idle timeout plus reaping on next start. Revisit once a
  real client is in the loop (Phase 4).

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

### Packaging

`uv` project, `requires-python >= 3.11` (tomllib in the stdlib; local toolchain
is 3.13). Pin `x402-avm[avm,httpx]==2.0.2` — the same version the Authen work was
verified against, and the version every claim in `CLAUDE.md` was measured on.
Console script `obol`, so the CLI and the MCP entry point ship together.

Closed source for now (decision 5): no `LICENSE`, no badges, no publish workflow.

### Network profiles are config, not constants

Learned from `D:/repos/Authen/config/node.example.toml`, and reinforced by the
stand-in ASA above. Each profile carries `caip2`, `slug`, `payment_asa`,
`decimals`, `algod_url`. Two traps worth restating in the config comments:

- `/discovery/resources` takes the **CAIP-2 id**; the `algorand-mainnet` slug
  silently returns `total: 0`. `/data/*` takes the slug instead.
- The x402 `asset` field is an ASA id **as a string**, not an int.

---

## 2. What to port, not rewrite

| From `D:/repos/Authen` | Into | Notes |
|---|---|---|
| `tools/pay_once.py` → `BuyerSigner` | `obol/signer.py` | Near-direct port. Signs only requested group indexes; the fee-payer txn belongs to the facilitator. |
| `tools/pay_once.py` → the 402 flow | `obol/x402.py` | Challenge decode, `create_payment_payload`, **`PAYMENT-SIGNATURE`** header, receipt decode. Note it reads the receipt from `PAYMENT-RESPONSE` *or* `X-PAYMENT-RESPONSE`; keep both. |
| `tools/algo.py` | `obol/algorand.py` | `account_info`, `asset_holding`, formatting, ALGOD urls. `asset_holding` returning `None` means *not opted in*, which is the failure that matters. |
| `authen/keys.py` | `obol/keys.py` | Ed25519 identity, Stellar/Algorand address encoding, **atomic write with `O_BINARY`** — see the bug note below. |
| `tools/pay_mainnet.py` | `obol/cli.py`, `obol/caps.py` | The preflight/guard pattern: check everything before spending, refuse without explicit confirmation. Its five safety rails are the spec for §3 Phase 3. |

**Port `pay_mainnet.py`'s rails, not just its shape.** They are the difference
between a wallet and a footgun, and one of them is not in `DESIGN.md` at all:

| Rail | Why it exists |
|---|---|
| Refuse a non-`https://` resource on mainnet | The facilitator catalogues the URL permanently on `/verify`. ~13% of the live index is loopback junk created this way. |
| **Refuse when payer == payTo** | Paying yourself is not a payment, and it is the first thing an anti-wash review looks for. Obol must refuse a challenge whose `payTo` is its own vault or session address. |
| Refuse a `payTo` that differs from what was expected | A substituted `payTo` sends money to a stranger and registers *their* merchant id. |
| Preflight ALGO, asset balance and opt-in before building anything | A predictable failure then costs nothing. |
| Require explicit confirmation to spend on mainnet | The one irreversible action in the system. |

**Carry the `O_BINARY` lesson.** `authen/keys.py` writes its seed with
`os.open(..., os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0))`.
Without `O_BINARY`, Windows text mode expands any `0x0A` in a 32-byte seed to
`0x0D 0x0A`; the file reads back 33 bytes and the wallet refuses to start. It
presents as an intermittent failure on roughly one install in eight.

---

## 3. Phases

Each phase ends with something demonstrable. Do not start the next until the
current one passes on testnet.

### Phase 1 — Wallet core (no x402, no MCP) — **DONE 2026-08-12**

Proved on testnet: bootstrap walked by hand, one open/close round trip
reconciled to the microunit, and all three crash windows recovered from a real
`os._exit` kill. 34 unit tests, no network. Evidence and the two design
corrections it forced are in §3.1 below.

`config.py`, `keys.py`, `algorand.py`, `session.py`, `ledger.py`, `cli.py`.

- Vault keypair generated on first run, seed written 0600 to the data dir
  (file-backed for v1 — decision 2), address printed
- `vault status` / `vault optin` — the bootstrap sequence in `DESIGN.md` §3.1.
  A fresh vault holds no ALGO and no asset slot; **it cannot receive USDC until
  it opts in, and cannot opt in until it holds ALGO.** Report which of the three
  steps the user is on, every time, rather than failing obscurely
- `session open` — one atomic group: fund ALGO → opt into the **profile's payment
  asset** → transfer balance
- `session close` — one atomic group: `close_assets_to` then `close_remainder_to`
- `ledger` persists every session address **at creation**, before funding
- `reap` sweeps orphaned sessions found in the ledger

**Done when:** against testnet, `session open` then `session close` returns the
full balance to the vault, and — separately — killing the process between open
and close, then running `reap`, recovers it. Test that crash path explicitly;
it is the one that silently loses money in production.

Fund the test vault from the Authen testnet treasury (ALGO plus stand-in ASA
`769120200`); no faucet is involved. Reconcile to the microunit — the sum of
vault plus session must be conserved across a full open/close cycle, less exactly
the fees actually paid.

#### What Phase 1 measured, and the two things it changed

**Cost per session is exactly as designed:** 0.005 ALGO, five transactions at the
0.001 minimum, confirmed across two full cycles (vault 0.599 → 0.594 → 0.589).
The 0.21 ALGO minimum balance came back whole every time. Note the fee split,
which is not obvious: the vault pays two fees at setup, the session pays one for
its own opt-in and two more at teardown out of its funding.

**Two corrections the design needed, both found by building rather than reading:**

1. **Session keys must be derived from the vault seed, not generated.** The
   design said memory-only with the address persisted — which cannot be swept,
   because closing an account requires *signing* from it. See `DESIGN.md` §2.1.
2. **A closed account does not 404.** algod keeps answering with a zeroed record,
   so a reaper keyed on existence re-closes accounts it already swept and fails
   on the fee. Test on holdings, not existence. See `DESIGN.md` §3.

**Also fixed:** all user-facing CLI output is ASCII. Em-dashes render as `?` on a
stock Windows console, and this is a tool people will run on Windows.

**Left for Phase 3, deliberately:** `caps.py` does not exist yet. `Caps` is
defined and loaded from config, but nothing enforces it — there is no spend path
to enforce it on until x402 lands.

### Phase 2 — x402 payment — **DONE 2026-08-12**

Paid from a session account against a loopback Authen node on testnet, and the
attestation verifies offline (`probes/verify_attestation.py`) — including a
negative control, since a verifier that cannot fail proves nothing.

**The design point worth keeping.** A 402 may offer several `accepts` entries,
and the SDK runs its own selector over them. Validating one entry and handing the
SDK the whole challenge would let it sign a *different* one — different payTo,
different amount, different asset — making every guard decorative. So the
challenge is narrowed to the single validated entry before it reaches
`create_payment_payload`: **validate then sign the same thing.**

Also confirmed: an x402 payment costs the buyer no ALGO. Two settlements moved
0.1 USDC and left the session's ALGO untouched.

**Deliberately still absent:** `caps.py`. `--max-price` and the configured
per-call cap are enforced in `x402.guard` because there is now a spend path and
leaving it uncapped would be careless; the daily counter, the allowlist and the
consent model are Phase 3.

### Phase 2 — x402 payment (original plan)

`signer.py`, `x402.py`.

- Port `BuyerSigner`
- `fetch(url, method, body)` — challenge, sign, replay, return body + receipt
- Refuses non-`402` challenge shapes rather than guessing
- Refuses a challenge whose `accepts[].asset` is not the profile's payment asset,
  naming both, rather than opting into something at spend time
- Refuses a challenge whose `payTo` is our own vault or session address

**The testnet target is Authen booted on loopback**, exactly as `pay_once.py`
does it — there is no deployed Authen testnet host. Two conditions, both from
`CLAUDE.md`: use a config whose `extra.tag` is **not** `x402-global-challenge`
(`node.local.toml` already sets a local tag), and accept that the loopback URL is
catalogued permanently against that merchant id. That is the price of a testnet
run and it is why this must never be done with the mainnet config loaded.

**Done when:** `obol fetch http://127.0.0.1:8402/api/v1/notarize` pays from a
session account and returns a signed attestation with the receipt printed, and
the attestation verifies offline against `/api/v1/identity`.

### Phase 3 — Caps and consent

`caps.py`.

- Per-call maximum, checked before signing
- Daily total, persisted, resets on UTC day boundary
- Optional payTo/host allowlist
- The five `pay_mainnet.py` rails from §2, always on — they are not caps the user
  can raise. Self-payment and non-https-on-mainnet are refusals, not warnings
- Mainnet requires an explicit profile selection plus per-spend confirmation
  until the MCP consent model replaces it in Phase 4
- Session balance is the chain-enforced backstop and needs no code

**Done when:** each cap refuses correctly and the refusal names which limit was
hit. Unit-testable without network.

### Phase 4 — MCP server

`mcp/server.py`, `mcp/tools.py`.

- `x402_fetch`, `wallet_status`, `wallet_funding_info`
- Session opened lazily on first paid call, not at startup
- Tool descriptions carry the honest caveats from `DESIGN.md` §7

**Done when:** installed in a real MCP client, an agent completes a paid
notarization against loopback Authen on testnet without the human touching a key.

### Phase 5 — The first mainnet payment

The demo, and the point of the whole exercise: an agent buys something real, from
a real merchant, with money it was given once and provisioned none of.

- Switch the profile to mainnet; target `https://authen.hvym.link/api/v1/notarize`
- Fund the vault with a few dollars of real USDC — vault bootstrap for real, and
  the first honest test of how much friction §3.1 actually leaves
- `obol` preflight must pass every rail before anything is signed
- Open a session, pay $0.05, close the session, reconcile

**Done when:** a settled mainnet txid, an attestation that verifies offline, and
a vault balance that reconciles to the microunit.

**Deadline interaction:** if this is not comfortably reachable by **2026-08-28**,
run `D:/repos/Authen/tools/pay_mainnet.py --pay --confirm` to close the gate and
let Obol take the second payment. The gate is Authen's, not Obol's, and Obol must
never be the reason it slips.

### Phase 6 — v1.1 funding paths

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

1. **Blocking the MCP event loop.** No longer an unknown (P0.1) but still a live
   risk: the payload build blocks for ~1 s and *anything* else that reaches for
   algod synchronously will do the same. Every blocking call goes through
   `asyncio.to_thread`, and nothing else in the process may call algod directly.
2. **Session boundary.** No clean end signal means balances sit in session
   accounts longer than intended. Mitigated by the reaper, not solved by it.
3. **The September 1 gate.** Twenty days from writing. Mitigated structurally,
   not by optimism: `pay_mainnet.py` already closes it, so Obol's schedule and
   Authen's deadline are independent. Keep them that way — Phase 5 states the
   fallback date explicitly.
4. **Paying ourselves.** Obol's demo merchant is Authen, which we own. One
   payment is the gate and is fine; a wallet that loops against its own endpoint
   is wash traffic and a disqualification risk. `CLAUDE.md`'s house rule — never
   generate volume for its own sake — is enforced in code by the payer ≠ payTo
   rail, and in judgement by not pointing benchmarks at Authen.
5. **MCP client variation.** Tool-approval behaviour differs between clients;
   the consent model in `DESIGN.md` §7 may need adjusting per client.
6. **Onramp minimums (v1.1).** Several providers sit at $5–15 minimum, which is
   large relative to a $5 session balance. Vault top-ups will be chunkier than
   session spends — design the funding UX around that, not against it.

**Retired:** *testnet USDC availability.* The stand-in ASA `769120200` exists and
is funded on both Authen testnet accounts; no faucet is on the critical path.

---

## 6. Definition of done for v1

- A developer with USDC on Algorand installs Obol, runs one command, and their
  agent can pay for x402 resources within a budget they set once.
- No key ever leaves the machine.
- An unclean exit loses nothing.
- The wallet works against any x402 resource, not only Authen.
- **At least one real mainnet settlement was made by Obol itself**, reconciled to
  the microunit, with the attestation verified offline.
