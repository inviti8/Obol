# Obol — agent brief

An **MCP server that gives an AI agent a disposable Algorand wallet**, so it can
pay for x402 resources without a human provisioning anything.

The name is the coin paid to the ferryman for passage: a small denomination, one
purpose, spent and gone.

## What Obol is, and is not

**Is:** a general-purpose x402 payment client exposed over the Model Context
Protocol. Give it a URL that answers `402`, and it pays and returns the body.

**Is not:** a client for [Authen](https://github.com/inviti8/Authen). Authen is
the reference integration and nothing more. This distinction is load-bearing —
see "Why not just an Authen client" below.

## Why this exists — the measured problem

Field data pulled live from the GoPlausible facilitator on **2026-08-12**
(re-measure before trusting; it moves weekly):

| Metric | Value |
|---|---:|
| Resources listed in the Bazaar | 1,204 |
| Merchants | 53 |
| Settlements | 288,110 |
| Volume | $22,447 |
| "Unique payers" | 450 |
| **Payers accounting for 90% of volume** | **13** |
| Top payer's share | 36% |

The top four payers made 77,217 / 36,322 / 29,854 / 14,857 settlements at
**exactly $0.10 each**. Those are scripted loops against known endpoints, not
agents shopping.

Two further findings, both verified rather than inferred:

- **Top-earning merchants are not in the Bazaar discovery index at all.** Their
  payTo addresses do not appear among the ~80 in `/discovery/resources`. So
  discovery is not what drives purchases; pre-arranged integrations are.
- **A listed payTo with several resources had received $0.00, ever.** Listing
  well brings nothing.

The conclusion that matters: **x402 has no buyer-side infrastructure.** A payment
requires an Algorand account, a USDC opt-in, a funded balance and transaction
signing. No agent framework ships any of that. The rail exists; nothing can
reach it.

That is the gap Obol fills. It is also why this is a better product than another
endpoint — the ecosystem has 1,204 sellers and approximately zero buyers.

## Why not just an Authen client

If Obol only pays Authen, then every settlement is one party we control paying
another party we control. On chain that is a round trip, and the competition's
leaderboard pool is explicitly subject to anti-wash review. Real customer money
arriving via Stripe does not change what an auditor sees.

Making the wallet general dissolves this: paying Authen becomes one of many
things it does, and any volume it generates against third-party resources is
genuine. It is also the stronger product — being the wallet everyone installs is
worth more than being one endpoint's SDK.

**House rule: never generate volume for its own sake.** The test for any feature
is whether you would build it if the leaderboard did not exist.

## Hard-won facts about x402 — do not rediscover these

All verified on the wire against `x402-avm` 2.0.2 and the live GoPlausible
facilitator. Each of these cost real debugging time in the Authen build.

**The request header is `PAYMENT-SIGNATURE`.** Not `PAYMENT`, not v1's
`X-PAYMENT`. Send the wrong name and the server sees *no payment at all*: it
re-challenges with a generic `"Payment required"` that is indistinguishable from
a rejection, while `/verify` returns `isValid: true` for the identical payload.
Diagnosing this by staring at the facilitator does not work.

**The client signer is signature-envelope agnostic.** `ClientAvmSigner.sign_transactions`
returns raw bytes which the SDK base64s without inspecting them — no `sig`/`lsig`
special-casing. This is what makes the LogicSig plan in `DESIGN.md` plausible.

**`extra.tag` sits inside `accepts[].extra`**, not at the top level.

**The buyer pays no transaction fee.** The facilitator sponsors it via `feePayer`
in the group. Confirmed on chain: settlement `axfer`s land with `fee: 0`. The
buyer still needs ALGO for minimum balance, but never for fees.

**Minimum balance is 0.1 ALGO for the account plus 0.1 per ASA held.** At
2026-08-12 prices that is about **1.6 cents** to stand up a USDC-capable account.
The friction in this ecosystem is *steps*, not cost.

**An account cannot receive an ASA it has not opted into.** The transfer is
rejected outright — there is no pending state to wait on. Opt in first, then ask
for tokens.

**`/discovery/resources?network=` needs the CAIP-2 id.** The `algorand-mainnet`
slug silently returns `total: 0`. `/data/*` endpoints take the slug instead.

**The facilitator auto-catalogs on `/verify`.** The first paid request registers
whatever `resourceUrl` it carries, permanently, keyed to the payer's merchant id.
This is why ~13% of the live index is `localhost` and `127.0.0.1` junk. It
matters less for a client than a server, but never point test traffic at a URL
you do not intend to keep.

## Chain scope

**Algorand only for v1.** Stellar is deferred, not dropped —
[Pakana](https://www.pakana.net/developers/) may provide x402 facilitation there
(ZK private payments on Stellar, early access). If it does, it is more
interesting than a second rail: it would break the vault→session funding link
natively, which is otherwise the weak point in Obol's unlinkability story.

One Ed25519 key is simultaneously a Stellar and an Algorand address, so the key
layer is already chain-agnostic. It is the payment protocol that differs.

## Related work in the estate

| Repo | Relevance |
|---|---|
| `D:/repos/Authen` | The reference integration. Its `tools/pay_once.py` and `tools/pay_mainnet.py` contain a working x402 client and `BuyerSigner`. Port, do not rewrite. |
| `D:/repos/kenter` | Additive n-of-n Ed25519 key composition, bearer instruments. The eventual answer to real unlinkability. |
| `D:/repos/heavymeta` | Flutter co-op wallet. Zero-custody invariants worth reading before designing float handling. |

## House rules

- **Verify before asserting.** Every number above was measured; re-measure it.
- **The float is the spend cap.** Do not build cryptography to protect an amount
  smaller than the cost of building it.
- **Never claim an attestation or payment proves more than it does.**
- **A session key is disposable by design.** If protecting one requires
  significant machinery, the design is wrong.
