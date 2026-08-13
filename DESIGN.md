# Obol — design

Read [`CLAUDE.md`](./CLAUDE.md) first for orientation and the x402 facts that
are expensive to rediscover.

**Written:** 2026-08-12. **Status:** design, nothing built.

---

## 1. The shape in one paragraph

An agent asks Obol to fetch a URL. Obol tries it, gets a `402`, signs an Algorand
USDC payment from a short-lived session account, replays the request with the
`PAYMENT-SIGNATURE` header, and returns the body. The human approved a session
budget once; individual payments under the cap do not interrupt them. When the
session ends the account is closed and its remaining value swept back.

## 2. Two-tier accounts

```
  Vault  (persistent, one per install)
    · Ed25519 key in the OS keychain, never leaves the machine
    · holds the balance the user funded
    · NEVER signs an x402 payment
    · the trust boundary
         │
         │  atomic group: fund ALGO → opt in USDC → transfer balance
         ▼
  Session  (ephemeral, one per agent session)
    · fresh keypair, memory only; address persisted for reaping
    · funded with a bounded balance
    · signs x402 payments
    · closed at session end, remainder swept to vault
```

**Why two tiers rather than one wallet:** a compromised session key loses the
session balance and nothing else. The vault key signs no payments, so it is never
handed to an x402 SDK, never in a request path, and can later move to hardware
without touching payment code.

**Honest limitation.** Sessions are unlinkable *to each other* by address, but the
funding transaction publicly links each one to the vault. This buys hygiene, not
anonymity. Breaking that link needs bearer instruments — see `D:/repos/kenter`,
which already has additive n-of-n Ed25519 composition. Out of scope for v1; do
not claim privacy the design does not deliver.

## 3. Session lifecycle, concretely

Algorand group transactions execute in order and state changes apply between
them, so setup is **one atomic group, one round**:

| # | Transaction | Sender | Purpose |
|---|---|---|---|
| 1 | `PaymentTxn` → session | vault | 0.21 ALGO: 0.1 account min + 0.1 ASA slot + fee headroom |
| 2 | `AssetTransferTxn` amt=0 | session | opt into USDC (ASA `31566704`) |
| 3 | `AssetTransferTxn` → session | vault | the session balance |

Teardown is a second group. **Order matters** — an account holding an ASA cannot
be closed:

| # | Transaction | Sender | Purpose |
|---|---|---|---|
| 1 | `AssetTransferTxn` `close_assets_to=vault` | session | return USDC, free the 0.1 ASA min balance |
| 2 | `PaymentTxn` `close_remainder_to=vault` | session | return ALGO, close the account |

**Cost per session:** 5 transactions × 0.001 ALGO ≈ **$0.0004**. Minimum balance
locked during the session is 0.21 ALGO (~1.7¢), fully reclaimed on close.

**Latency:** two rounds, roughly 3 s each. Noticeable at session start, so keep a
session alive across calls rather than opening one per call.

### Closing an account: Algorand vs Stellar

Functionally the same as Stellar's `ACCOUNT_MERGE`, mechanically different in a
way that matters for security.

| | Stellar | Algorand |
|---|---|---|
| Mechanism | `ACCOUNT_MERGE`, a distinct operation | `close_remainder_to`, a **field on any payment** |
| Sub-holdings | trustlines must be removed first | ASAs must be closed first (`close_assets_to`) |
| Reserve model | 0.5 XLM base + 0.5 per subentry | 0.1 ALGO base + 0.1 per ASA |
| Result | source account deleted, balance to destination | account removed from state, balance to destination |

**The consequence: closing is not a privileged operation on Algorand.** Any
ordinary payment can carry `close_remainder_to` and empty the account. There are
three such escape hatches on the transaction types a session key signs:

| Field | On | Effect if set by an attacker |
|---|---|---|
| `close_remainder_to` | `PaymentTxn` | drains all ALGO, closes the account |
| `close_assets_to` | `AssetTransferTxn` | drains the entire USDC balance |
| `rekey_to` | both | hands over the account permanently |

An x402 payment is an `AssetTransferTxn`, so `close_assets_to` and `rekey_to` are
both reachable by anything holding the session key. This is why a spend policy
that only checks amount and receiver is worthless — see §6.

### The reaper is not optional

A session that dies uncleanly — crash, kill, power loss — strands 0.21 ALGO plus
its remaining USDC in an orphaned account. **Persist every session address to
disk at creation**, and sweep orphans on next start. Without this, Obol leaks
money on every unclean exit, silently.

## 4. Spend controls

**v1 issues no money to anyone.** A vault is funded by sending USDC to its
address — the same path whether the money is the user's or ours. Demo and
marketing wallets are covered by that, with no dispenser, no gating, no ceiling
and no accounting subsystem. Someone sends USDC to an address; that is the whole
mechanism.

Terminology, since the word was overloaded in an earlier draft: **session
balance** is the USDC sitting in a session account. A **grant** would be money we
give a stranger. v1 has the first and none of the second.

### What bounds a loss

The session balance, enforced by the chain rather than by our code — a
compromised session key can spend what is in that account and nothing more.
Everything else is defence in depth:

| Control | Enforced | Default |
|---|---|---|
| Session balance | on chain, by balance | user-set, suggest $5 |
| Per-call maximum | in process, before signing | $0.50 |
| Daily total | in process, persisted counter | user-set |
| payTo / host allowlist | in process | off |

These protect the user's own money, so they earn their place regardless of who
funded the wallet.

### If a self-serve trial is ever added

Not v1. Recorded because the reasoning is easy to get wrong, and the wrong
instinct is expensive.

**The attack is not key extraction.** An attacker installs Obol *n* times,
collects *n* grants, and spends them — pointing a general-purpose wallet at
their own x402 endpoint turns the grant into their revenue. No key is stolen.
Hardening custody changes nothing, because the exposure is **issuance**.

Controls, in order of effectiveness:

1. **Cap the aggregate, not the per-agent.** If only $2,000 is ever in
   circulation, maximum loss is $2,000 regardless of agent count. This is the
   only control that actually bounds the risk.
2. **Gate issuance the boring way** — card on file, one grant per verified
   identity. A free-tier abuse problem; no cloud provider solved it with
   cryptography.
3. **Track granted spend separately** from user-funded spend. Volume we paid for
   is not organic volume, and the distinction cannot be retrofitted once the
   transactions are on chain.

Revisit when a self-serve trial becomes the growth bottleneck. Until then,
manual grants — which is just sending USDC to an address, and scales to the
hundreds.

## 5. Funding the vault — the actual product goal

The thesis is a **minimum-friction pay surface for an agent**. Removing account
setup, opt-in and signing is only half of it: a user who cannot easily *get*
USDC onto Algorand is still stuck. This section is therefore core scope, not a
later nicety.

### Stripe cannot do this, and that is a finding

Stripe Crypto Onramp supports Bitcoin, Ethereum, Solana, Polygon, Stellar,
Avalanche, Base, Aptos, Optimism, Worldchain and XRPL — with USDC specifically on
Solana, Polygon, Avalanche and Base. **Algorand is not on the list** (checked
2026-08-12; re-check, it moves).

So "add Stripe" is not the task. The task is **embed an onramp that delivers
USDC to an Algorand address.** Providers that do: Banxa, MoonPay, Sardine,
Transak, Wyre — all five are already aggregated by Pera Onramp, which is proof
the integration path works rather than a recommendation to use Pera specifically.

Worth noting Stripe *does* support Stellar. If Pakana's x402 facilitation
materialises, the funding story on that chain is materially easier than on
Algorand — a point in Stellar's favour for v2 that has nothing to do with the
protocol.

### The legal boundary — never be in the money path

This is the constraint that decides the architecture.

| Pattern | What it is | Verdict |
|---|---|---|
| User buys USDC through a licensed onramp, delivered to **their own** vault address | we facilitate a link | **Do this** |
| We take fiat and send USDC | selling crypto for money | money transmission; licensing in ~50 states |
| We hold a pooled balance and pay on their behalf | custody | money transmission, plus the payer is us again |

Only the first is viable for a small team, and it is also the simplest. **Obol
embeds an onramp; it does not build one.** The provider does KYC, takes the card,
and is the money transmitter. We never touch fiat or hold anyone's crypto.

This also keeps the payer genuinely third-party, which is what stops funded usage
from looking like a round trip (§4, and `CLAUDE.md` on why Obol is not an Authen
client).

### The flow

```
wallet_funding_info()
  → returns an onramp URL with the vault address and a suggested amount
    pre-filled, plus the current balance
  → human opens it, does KYC once, pays by card
  → USDC lands directly in the vault
  → Obol polls the balance and reports when it clears
```

### The friction floor, stated honestly

**First purchase requires KYC** — identity verification, a few minutes, a photo
of an ID. That is regulatory and cannot be engineered away by anyone who is not
themselves licensed. Subsequent top-ups are fast.

So the real shape is: *one interactive setup of a few minutes, then the agent
spends autonomously within its caps, indefinitely.* That is a genuine and
defensible claim. "Zero friction" is not, and should never appear in the copy.

### Why this matters beyond Obol

Getting USDC onto Algorand is harder than onto Base or Solana, and no major
onramp treats it as a first-class destination. That is a structural headwind for
every x402 resource on this chain, and it is a plausible partial explanation for
the field data in `CLAUDE.md`: 1,204 listed resources, but 13 wallets accounting
for 90% of volume. The buyers who exist are the ones who were determined enough
to solve funding themselves.

Obol cannot fix the chain's onramp coverage. It can remove every step after it.

## 6. LogicSig — scope it, then probably defer it

An Algorand **LogicSig** is a signed program that approves only transactions
matching its logic. As a *contract account* the address is the hash of the
program and **there is no private key at all** — maximally secure, nothing to
steal. As a session-key replacement that is genuinely attractive.

Two facts established:

- **The x402 client passes signer bytes through opaquely** — no `sig`/`lsig`
  special-casing (verified in `x402-avm` 2.0.2). The client side is fine.
- **Unknown: does the GoPlausible facilitator accept a non-`sig` envelope** at
  `/verify` and `/settle`?

### The probe — cheap, do it regardless

An afternoon on testnet: build an `exact` payment, sign with a LogicSig instead
of a raw key, submit, observe. Worth knowing the answer even if we do not build
on it, because it is a one-line question with a permanent answer.

### The build — not cheap, and the reason is §3

A correct policy must close **every** escape hatch, not just check amount and
receiver:

```
assert TypeEnum        == axfer
assert XferAsset       == USDC_ASA
assert AssetReceiver   ∈ allowlist
assert AssetAmount     <= cap
assert AssetCloseTo    == ZeroAddress   # else: drains the whole balance
assert CloseRemainderTo == ZeroAddress  # else: drains ALGO, closes account
assert RekeyTo         == ZeroAddress   # else: account taken permanently
assert GroupSize / index constraints    # else: replayed inside a hostile group
```

Omitting any one of the last four makes the policy decorative. This is a
well-known class of Algorand LogicSig bug, and it is exactly the kind of code
that looks finished while being trivially bypassable. Realistically that is a
week of work plus an audit, not an afternoon.

**Recommendation: probe now, defer the build.** At a $5 session balance the payoff
does not justify the risk of getting TEAL subtly wrong — and the aggregate
trial ceiling in §4 bounds total exposure far more effectively than per-session
hardening does. Revisit if session balances grow by an order of magnitude, or
if a real customer wants enforced spend policy as a feature.

If the probe says the facilitator rejects LogicSig envelopes, the question closes
permanently and balance caps carry the risk alone — which is the documented,
deliberate position rather than an accident.

## 7. MCP surface

Deliberately small. One tool is the product.

```
x402_fetch(url, method="GET", body=None, max_price_usdc=None)
    Fetch a URL, paying if it challenges. Returns body, price paid, txid,
    and the settlement receipt. Refuses above max_price_usdc or the
    configured per-call cap.

wallet_status()
    Vault address, ALGO and USDC balance, active session and its remaining
    balance, spend today against the daily cap.

wallet_funding_info()
    Address and instructions, for the human. Includes the ASA opt-in note,
    since an un-opted-in account silently cannot receive USDC.

x402_discover(query=None, max_price_usdc=None)
    Search the Bazaar. Honest caveat in the tool description: discovery does
    not reflect where volume actually goes (CLAUDE.md), so treat results as a
    catalogue, not a recommendation.
```

No `authen_notarize` tool. Authen is reached through `x402_fetch` like anything
else — the moment Obol has first-class Authen verbs it stops being a wallet.

### Consent model

Per-payment approval prompts destroy the value: an agent that must ask before
every $0.05 call is not autonomous. Per-session approval is the right unit —
**the human approves a budget once, and spends inside it proceed silently.**
That matches how people already think about petty cash, and the loss ceiling is
explicit at approval time.

Escalate to the human only when: the session balance is exhausted, a single call
exceeds the per-call cap, or a payTo is outside an allowlist that the user
enabled.

## 8. v1 scope

**Ships:**

- Algorand mainnet + testnet
- Python (rationale below)
- Vault key in the OS keychain via `keyring`
- Ephemeral session accounts with atomic setup/teardown, plus the reaper
- `x402_fetch`, `wallet_status`, `wallet_funding_info`
- Spend caps enforced in process
- **Embedded onramp** — one provider, vault address pre-filled (§5)
- Any vault can also be funded by sending USDC to its address directly — ours or
  the user's, same path, no feature required (§4)

**Does not ship:** any automatic dispenser or granted balance, LogicSig policy,
Stellar, bearer instruments, multiple onramp providers with quote comparison.

### What v1 does not solve, stated plainly

The KYC step in §5. Everything after it is automated; that one is regulatory and
belongs to the onramp provider.

Do not let "we should give people money to try it" become the answer to "people
cannot easily buy USDC". Those are different problems, and only the second one is
real — it is solved by the embedded onramp, not by a grant.

### Python, not TypeScript

`npx` is the friendlier MCP install path and TypeScript would reach more users.
Choosing Python anyway, because the x402 client path in `x402-avm` has been
driven end to end twice in the Authen build, the `BuyerSigner` implementation and
the signer protocol are understood, and `tools/pay_once.py` / `tools/pay_mainnet.py`
are near-direct ports. Re-deriving all of that against a JS SDK is avoidable risk
on a short clock.

Revisit for v2 once the wallet abstraction has proven itself against one rail.

## 9. Open questions

1. **Does the facilitator accept LogicSig envelopes?** Probe early — §5 says
   defer the build regardless, but the answer is permanent and costs an afternoon.
2. **Session boundary** — MCP has no session-end signal on all transports. Idle
   timeout plus reaping on next start is probably the answer, but it means
   sessions outlive their usefulness and hold a balance longer than necessary.
3. **Does `x402-avm` work cleanly inside an MCP server's event loop?** The Authen
   work used `x402ClientSync`. MCP servers are async; the sync client may need a
   thread or the async variant needs its own validation.
4. **Which onramp provider for v1?** Banxa, MoonPay, Sardine, Transak and Wyre
   all reach Algorand. Decide on widget quality, geographic coverage, minimum
   purchase and fee, not on brand. Minimums matter: several sit around $5-15,
   which is large relative to a $5 session balance.
5. **Multiple concurrent sessions per vault** — needed? Adds nonce/ordering
   concerns on vault-signed funding transactions.

## 10. First tasks, in order

1. LogicSig facilitator probe on testnet (§5). Cheap, and the answer is permanent.
   Do not block the build on it — §5 recommends deferring the policy work.
2. Port `BuyerSigner` and the 402 flow from Authen; prove `x402_fetch` against
   Authen's testnet endpoint.
3. Session lifecycle: atomic setup, atomic teardown, reaper. Test the crash path
   explicitly — kill the process mid-session and confirm the next start recovers
   the balance.
4. Wrap in an MCP server; verify against a real MCP client.
5. Embedded onramp (§5) — pick a provider, prove a card purchase lands USDC in a
   vault address. This is the half of the thesis that is not about signing.
6. Spend caps and the consent model.
