---
description: How to get an Obolus wallet from empty to able to pay, and how to read a payment that failed for lack of funds. Use when wallet_status or x402_fetch reports the wallet is not ready, when the user asks how to fund or top up the wallet, or when a payment fails and you need to work out whether funding is actually the cause.
---

# Getting an Obolus wallet ready to spend

The wallet cannot fund itself. This is deliberate — Obolus never holds a claim on
the user's money and never asks for a card. Your job is to tell the human exactly
what to send and where, in the right order, and to know when funding is *not* the
problem.

Call `wallet_funding_info` first. It returns `current_step`, `next_action` and the
address, and it is the authority — everything below explains *why* it says what it
says, so you can answer follow-up questions rather than just relaying it.

## The three steps are in a forced order

The chain enforces this, not Obolus. Doing them out of order does not fail gracefully.

**1 — Send ALGO to the vault address.** An Algorand account must hold a minimum
balance before it can do anything at all: 0.1 ALGO for the account, plus 0.1 for
each asset it holds. So a USDC-capable vault needs **0.2 ALGO**, and a little over
that is sensible.

**2 — Opt the vault into the payment asset.** Obolus does this itself; it just needs
step 1 done first, because opting in creates the asset holding that the second
0.1 ALGO covers.

**3 — Send USDC.** Only now.

**Why the order cannot be rearranged:** an Algorand account cannot receive an asset
it has not opted into. The transfer is *rejected outright* — there is no pending
state, no holding area, and nothing to claim later. USDC sent at step 1 does not
arrive slowly; it does not arrive.

This matters most when the user funds from an onramp or an exchange, because those
deliver USDC and not ALGO. Step 1 still has to happen separately, and the user will
not expect that. Say so before they send anything.

## What this actually costs

About 0.2 ALGO of minimum balance — cents, not dollars, though re-check the price
rather than quoting a figure you were not given. It is not a fee: minimum balance
stays in the account and is recoverable when the account is closed.

**The buyer pays no transaction fee.** The x402 facilitator sponsors it. Settlement
transfers land with `fee: 0`. So the ALGO is for the minimum balance and nothing
else — do not tell the user they need ALGO "for gas", because they do not, and it
changes how much they think they need to send.

The friction here is the number of steps, not the amount of money. Treat a confused
user as the expected case.

## Reading a failure correctly

When a payment fails, work out which of these it is before suggesting a top-up:

- **Vault not ready** — `wallet_status` says so. Funding is the answer; go to the
  three steps.
- **A cap refused it** — the per-call, daily or session cap. The wallet has money and
  deliberately did not spend it. Adding funds changes nothing; the user has to raise
  the cap, and should be told what it currently is rather than nudged to raise it.
- **The merchant is not on the allowlist** — same shape. Not a funding problem.
- **The resource was unreachable** — nothing was spent, and nothing is stranded. Say
  that plainly; a user who has just watched a payment fail will assume otherwise.

The distinction is worth getting right. Telling someone to send more money when the
wallet refused on a cap is both wrong and expensive.

## Vault and session are different things

The **vault** is what the user funds. A **session** is a short-lived account funded
from the vault, and payments come from the session, never from the vault directly.
That is what bounds the loss on any single call to the session balance.

So "the wallet has money but the payment failed" is a coherent state. Check whether
the vault is funded and whether a session is open before concluding anything.

## Two things not to do

**Never point `OBOLUS_DATA_DIR` inside the plugin directory.** The vault seed lives
there, plugin directories are replaced on update, and the seed is the only way back
to money already on chain. The default location is per-user and outside the plugin
for exactly this reason. If the user wants a custom path, it must be somewhere
durable — and pointing it at a *new* empty directory means a new wallet, not a
relocated one.

**Never overstate what a payment proves.** A settled payment proves that it
settled. It does not prove the resource was correct, honest, or worth its price.
The tool descriptions say this too; keep saying it.

## Switching to mainnet

The plugin ships `OBOLUS_NETWORK=testnet` on purpose. Mainnet is never disabled —
Obolus exists to make real payments — but it should never be reached by forgetting
to choose. Changing it spends real money on the next paid call, so confirm the user
means it, and note that mainnet and testnet keep separate ledgers: a funded testnet
vault tells you nothing about the mainnet one.
