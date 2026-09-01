# Obolus — agent brief

An **MCP server that gives an AI agent a disposable Algorand wallet**, so it can
pay for x402 resources without a human provisioning anything.

The name is the coin paid to the ferryman for passage: a small denomination, one
purpose, spent and gone.

## What Obolus is, and is not

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

That is the gap Obolus fills. It is also why this is a better product than another
endpoint — the ecosystem has 1,204 sellers and approximately zero buyers.

## Why not just an Authen client

If Obolus only pays Authen, then every settlement is one party we control paying
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
for tokens. This applies to the vault as much as to a session — see `DESIGN.md`
§3.1, where it forces a three-step bootstrap in a fixed order.

**Never hardcode the payment ASA.** Mainnet USDC is `31566704`, but the Authen
testnet rail runs on a self-minted 6-decimal stand-in (`769120200`) because the
faucet rate-limited during that build; both accounts hold zero real testnet USDC
(`10458941`). The `exact` scheme takes any ASA id, so the asset is a network
profile setting checked against `accepts[].asset`, not a constant.

**`/discovery/resources?network=` needs the CAIP-2 id.** The `algorand-mainnet`
slug silently returns `total: 0`. `/data/*` endpoints take the slug instead.

**The facilitator auto-catalogs on `/verify`.** The first paid request registers
whatever `resourceUrl` it carries, permanently, keyed to the payer's merchant id.
This is why ~13% of the live index is `localhost` and `127.0.0.1` junk. It
matters less for a client than a server, but never point test traffic at a URL
you do not intend to keep.

## One hard-won fact about the MCP layer

**A tool parameter annotated `str | None` cannot receive a JSON string.** The
framework's `pre_parse_json` runs `json.loads` on every string argument whose
annotation is not *exactly* `str`, to help clients that stringify objects. So
`x402_fetch(body='{"a":1}')` arrived as a dict and pydantic rejected it as "not a
valid string" - the obvious call, for the commonest paid-endpoint body type, was
the one that could not be made. Found on 2026-09-01 against a live $0.25 endpoint
(`D:/authen_mainnet_launch/RUNLOG.md`), where `body_file` was the only way through.

`body` is therefore `str | dict | list | None`, and objects are serialised and
labelled `application/json` in `encode_body`. **Do not narrow it back.** The same
quirk also turns `body="null"` into no body at all, silently - there is no
annotation that avoids that while still accepting objects.

## Chain scope

**Algorand only for v1.** Stellar is deferred, not dropped —
[Pakana](https://www.pakana.net/developers/) may provide x402 facilitation there
(ZK private payments on Stellar, early access). If it does, it is more
interesting than a second rail: it would break the vault→session funding link
natively, which is otherwise the weak point in Obolus's unlinkability story.

One Ed25519 key is simultaneously a Stellar and an Algorand address, so the key
layer is already chain-agnostic. It is the payment protocol that differs.

## Mainnet is done — 2026-08-13/14

**Obolus made its own first mainnet payments, and the backstop was never used.**
Three settlements against the live Authen node, all driven by an agent calling
`x402_fetch` over MCP, not by the CLI and not by `tools/pay_mainnet.py`:

| When (UTC) | Resource | Amount | Session | Txid |
|---|---|---:|---|---|
| 2026-08-13 23:27 | `/api/v1/notarize` | $0.05 | 1 · `IBDY7UHB…HIE55A` | `YC3JCYIY…VMCDQ` |
| 2026-08-14 03:27 | `/api/v1/notarize` | $0.05 | 2 · `HR4GEKYB…ILZEHU` | `HHUR6UUD…WH7TA` |
| 2026-08-14 03:34 | `/api/v1/c2pa/sign` | $0.15 | 2 · `HR4GEKYB…ILZEHU` | `7C457WXN…4YFMQ` |

Both sessions opened, spent and swept closed; the ledger reconciles against the
chain. **Every settlement landed with `fee: 0`** — the facilitator sponsorship
claimed in §"Hard-won facts" now holds on mainnet, not just testnet.

The competition gate is closed. Authen appears on the facilitator leaderboard at
rank 48 of **97** mainnet merchants, `challenge: true`, 3 settles / 5 verifies /
$0.25 volume. The 2026-09-01 deadline and the 08-28 fallback date are both moot.

Not closed, and **not ours to fix**: Authen is still `bazaar: false` and absent
from the discovery catalog. The facilitator admitted no new resource *from any
merchant* in the ~12 h spanning the first payment, so this is not caused by
Authen's declaration. Do not re-point `resourceUrl` to try to force it — the
catalog is permanent and the leaderboard aggregates by payTo, so a second URL
fragments the entry instead of repairing it.

**The full record is `D:/authen_mainnet_launch/RUNLOG.md`**, with the API-level
analysis in `AUTHEN_API_REPORT.md` beside it. Read the run log before re-deriving
anything about the facilitator: it corrects two things this file got wrong. The
`/data/merchants` and `/data/leaderboards` endpoints **hard-cap at 50 rows
whatever `limit` says**, with no truncation signal — read `total`, or paginate
with `offset`, or you will silently ask a narrower question than you think. And a
POST resource must declare `bodyType`, not `queryParams`, to be cataloged at all.

**Development still happens on testnet.** Mainnet is guarded, not disabled.

**Renamed Obol -> Obolus, 2026-08-14.** Three unrelated projects shipped as
"Obol" in this exact market: obol.sh (an x402 developer platform on Base),
`dev.fly.obol-x402/obol` (an x402 MCP server, already in the MCP Registry we
publish to), and Obol Network (Ethereum DVT staking, which owns the word in
crypto search). Renamed before the first Registry submission, because a Registry
entry claims the name and PyPI distributions cannot be renamed - after
publishing, the same move costs a deprecated entry, a second package and broken
links. Same word, same ferryman, no collisions.

Two things deliberately did NOT change, and both are load-bearing:

  * `SESSION_INFO = b"obol-session-v1"` in `keys.py`. It is an HMAC domain
    separator, not a name. Change a byte and the same vault seed derives a
    different set of session accounts - funds in existing sessions become
    underivable and the ledger stops matching what the code reproduces.
  * The data directory. `default_data_dir()` prefers the new location but falls
    back to a legacy `Obol`/`.obol` directory **when it actually holds a seed**,
    and `OBOL_DATA_DIR` / `OBOL_NETWORK` still work alongside the `OBOLUS_*`
    spellings. The seed is the only way back to money already on chain, so a
    rename that silently pointed somewhere empty would present as a working
    install with an empty wallet.

**Open source, decided 2026-08-13.** Published to PyPI as **`obolus`**
(account `HVYM`); the import package is `obolus`. Release is tag-driven -
`.github/workflows/release.yml` runs the tests on three OSes, checks the tag
against `pyproject.toml`, then publishes. `server.json` registers it with the MCP
Registry as `io.github.inviti8/obolus`.

**The licence is still undecided and is the one thing blocking publication.** A
public repo with no `LICENSE` is "all rights reserved" by default, which is worse
than either choice because it reads as open while granting nothing.

This reversed an earlier closed-source default. The argument that settled it is
already in this file: the ecosystem has over a thousand sellers and approximately
zero buyers, and **being the wallet everyone installs is worth more than being one
endpoint's SDK**. Distribution reach is the strategy, not a nicety, and closed
source was in direct tension with it.

## Related work in the estate

| Repo | Relevance |
|---|---|
| `D:/repos/Authen` | The reference integration, live at `authen.hvym.link`. Its `tools/pay_once.py` and `tools/pay_mainnet.py` contain a working x402 client and `BuyerSigner`; `config/node.example.toml` is the model for network profiles. Port, do not rewrite. Formerly `PintheonV2` — that path is gone. |
| `D:/repos/kenter` | Additive n-of-n Ed25519 key composition, bearer instruments. The eventual answer to real unlinkability. |
| `D:/repos/heavymeta` | Flutter co-op wallet. Zero-custody invariants worth reading before designing float handling. |

## House rules

- **Verify before asserting.** Every number above was measured; re-measure it.
- **The float is the spend cap.** Do not build cryptography to protect an amount
  smaller than the cost of building it.
- **Never claim an attestation or payment proves more than it does.**
- **A session key is disposable by design.** If protecting one requires
  significant machinery, the design is wrong.
