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
         │  atomic group: fund ALGO → opt in USDC → transfer float
         ▼
  Session  (ephemeral, one per agent session)
    · fresh keypair, memory only; address persisted for reaping
    · funded with a bounded float
    · signs x402 payments
    · closed at session end, remainder swept to vault
```

**Why two tiers rather than one wallet:** a compromised session key loses the
session float and nothing else. The vault key signs no payments, so it is never
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
| 3 | `AssetTransferTxn` → session | vault | the float |

Teardown is a second group. **Order matters** — an account holding an ASA cannot
be closed:

| # | Transaction | Sender | Purpose |
|---|---|---|---|
| 1 | `AssetTransferTxn` `close_assets_to=vault` | session | return USDC, free the 0.1 ASA min balance |
| 2 | `PaymentTxn` `close_remainder_to=vault` | session | return ALGO, close the account |

**Cost per session:** 5 transactions × 0.001 ALGO ≈ **$0.0004**. Minimum balance
locked during the session is 0.21 ALGO (~1.7¢), fully reclaimed on close.

**Latency:** two rounds, roughly 3 s each. Noticeable at session start, so keep a
session alive across calls rather than per-call.

### The reaper is not optional

A session that dies uncleanly — crash, kill, power loss — strands 0.21 ALGO plus
its remaining USDC in an orphaned account. **Persist every session address to
disk at creation**, and sweep orphans on next start. Without this, Obol leaks
money on every unclean exit, silently.

## 4. Float security

The instinct to protect session keys with cryptography is misdirected. Work the
threat model:

**The attack that matters is not key extraction.** If Obol ever grants a free
float, an attacker does not need to steal a key — they install it *n* times,
collect *n* grants, and spend them. If the wallet is general-purpose (which it
must be), they point it at their own x402 endpoint and the grant becomes their
revenue. Perfect custody hardening changes nothing.

The exposure is **issuance**, not custody. Controls, in order of effectiveness:

1. **Do not grant float.** User-funded wallets have nothing to Sybil. A million
   agents funding themselves is revenue.
2. **Cap the aggregate, not the per-agent.** If only $2,000 of trial float is ever
   in circulation, maximum loss is $2,000 regardless of agent count.
3. **Gate issuance the boring way** — card on file, one grant per payment method.
   This is a free-tier abuse problem; no cloud provider solved it with crypto.

**The session float is itself the primary spend cap**, enforced by the chain
rather than by our code. Everything else is defence in depth:

| Control | Enforced | Default |
|---|---|---|
| Session float | on chain, by balance | user-set, suggest $5 |
| Per-call maximum | in process, before signing | $0.50 |
| Daily total | in process, persisted counter | user-set |
| payTo / host allowlist | in process | off |

## 5. The LogicSig question — probe before designing around it

An Algorand **LogicSig** is a signed program that approves only transactions
matching its logic. If a session key were a LogicSig constrained to "USDC
transfers, to whitelisted receivers, under $X", then extracting it would degrade
theft into *using the trial*.

Two facts already established:

- **The x402 client passes signer bytes through opaquely** — no `sig`/`lsig`
  special-casing (verified in `x402-avm` 2.0.2). The client side is fine.
- **Unknown: does the GoPlausible facilitator accept a non-`sig` envelope** at
  `/verify` and `/settle`?

**This is the first task in the repo**, because the answer changes the security
model. It is a short testnet probe: build an `exact` payment, sign it with a
LogicSig instead of a raw key, submit, observe.

- Accepted → on-chain spend policy nearly free; per-session keys become
  non-stealable in any meaningful sense.
- Rejected → float caps carry the risk alone, which is acceptable at $5 but
  should be a documented decision rather than a discovery.

## 6. MCP surface

Deliberately small. One tool is the product.

```
x402_fetch(url, method="GET", body=None, max_price_usdc=None)
    Fetch a URL, paying if it challenges. Returns body, price paid, txid,
    and the settlement receipt. Refuses above max_price_usdc or the
    configured per-call cap.

wallet_status()
    Vault address, ALGO and USDC balance, active session and its remaining
    float, spend today against the daily cap.

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

Escalate to the human only when: the session float is exhausted, a single call
exceeds the per-call cap, or a payTo is outside an allowlist that the user
enabled.

## 7. v1 scope

**Ships:**

- Algorand mainnet + testnet
- Python (rationale below)
- Vault key in the OS keychain via `keyring`
- Ephemeral session accounts with atomic setup/teardown, plus the reaper
- `x402_fetch`, `wallet_status`, `wallet_funding_info`
- Spend caps enforced in process
- User funds the vault directly

**Does not ship:** Stripe top-ups, LogicSig policy, Stellar, free trial float,
bearer instruments.

### Python, not TypeScript

`npx` is the friendlier MCP install path and TypeScript would reach more users.
Choosing Python anyway, because the x402 client path in `x402-avm` has been
driven end to end twice in the Authen build, the `BuyerSigner` implementation and
the signer protocol are understood, and `tools/pay_once.py` / `tools/pay_mainnet.py`
are near-direct ports. Re-deriving all of that against a JS SDK is avoidable risk
on a short clock.

Revisit for v2 once the wallet abstraction has proven itself against one rail.

## 8. Open questions

1. **Does the facilitator accept LogicSig envelopes?** Gates §5. Probe first.
2. **Session boundary** — MCP has no session-end signal on all transports. Idle
   timeout plus reaping on next start is probably the answer, but it means
   sessions outlive their usefulness and hold float longer than necessary.
3. **Does `x402-avm` work cleanly inside an MCP server's event loop?** The Authen
   work used `x402ClientSync`. MCP servers are async; the sync client may need a
   thread or the async variant needs its own validation.
4. **Trial float: yes or no for v1?** The design says no. If marketing wants one,
   §4 controls are mandatory, not optional.
5. **Multiple concurrent sessions per vault** — needed? Adds nonce/ordering
   concerns on vault-signed funding transactions.

## 9. First tasks, in order

1. LogicSig facilitator probe on testnet (§5) — gates the security model.
2. Port `BuyerSigner` and the 402 flow from Authen; prove `x402_fetch` against
   Authen's testnet endpoint.
3. Session lifecycle: atomic setup, atomic teardown, reaper. Test the crash path
   explicitly — kill the process mid-session and confirm the next start recovers
   the float.
4. Wrap in an MCP server; verify against a real MCP client.
5. Spend caps and the consent model.
