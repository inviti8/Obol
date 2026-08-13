"""Algod access, account state, and the two transaction groups a session needs.

Everything in here blocks. That is fine in the CLI and NOT fine in the MCP server
- P0.1 measured a ~900 ms event-loop stall from a single `suggested_params()` -
so every caller inside the async server must go through `asyncio.to_thread`. No
function in this module may be awaited directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from algosdk import transaction
from algosdk.error import AlgodHTTPError
from algosdk.v2client import algod

from .config import NetworkProfile
from .keys import Key

MIN_FEE_MICRO = 1_000


def client(profile: NetworkProfile) -> algod.AlgodClient:
    return algod.AlgodClient("", profile.algod_url)


def suggested_params(cli: algod.AlgodClient) -> transaction.SuggestedParams:
    """Flat minimum fee, always.

    Fee pooling would let one transaction cover another's fee, which is what makes
    the facilitator's sponsorship work. We never rely on it inside our own groups:
    each transaction pays its own way, so a partially-applied group is impossible
    to reason about wrongly.
    """
    sp = cli.suggested_params()
    sp.flat_fee = True
    sp.fee = MIN_FEE_MICRO
    return sp


def account_info(cli: algod.AlgodClient, address: str) -> dict[str, Any] | None:
    """Account state, or None if the account does not exist on chain.

    A never-funded address is a 404, not an error. It is also the normal state of a
    brand-new vault, so it must not read as a failure.
    """
    try:
        return cli.account_info(address)
    except AlgodHTTPError as exc:
        if "no accounts found" in str(exc).lower() or "404" in str(exc):
            return None
        raise


def asset_holding(info: dict[str, Any] | None, asa: int) -> dict[str, Any] | None:
    """The account's holding of `asa`, or None if it is NOT OPTED IN.

    Opt-in is what matters, not balance. An account cannot receive an ASA it has
    not opted into - the transfer is rejected outright, there is no pending state
    to wait on - so None here means "money sent to this address will bounce".
    """
    if not info:
        return None
    for h in info.get("assets", []):
        if int(h.get("asset-id", -1)) == asa:
            return h
    return None


@dataclass(frozen=True)
class AccountState:
    exists: bool
    algo_micro: int
    min_balance_micro: int
    opted_in: bool
    asset_micro: int

    @property
    def spendable_algo_micro(self) -> int:
        return max(0, self.algo_micro - self.min_balance_micro)

    @property
    def nothing_to_recover(self) -> bool:
        """True when there is no point sweeping this account.

        Deliberately not `not exists`. A closed Algorand account does not start
        404ing - algod goes on answering for it with a zeroed record - so the only
        honest test for "is there anything here" is whether it holds anything.
        """
        return not self.exists or (self.algo_micro == 0 and self.asset_micro == 0)


def account_state(cli: algod.AlgodClient, address: str, asa: int) -> AccountState:
    info = account_info(cli, address)
    holding = asset_holding(info, asa)
    return AccountState(
        exists=info is not None,
        algo_micro=int((info or {}).get("amount", 0)),
        min_balance_micro=int((info or {}).get("min-balance", 0)),
        opted_in=holding is not None,
        asset_micro=int((holding or {}).get("amount", 0)),
    )


def build_optin(sp, address: str, asa: int) -> transaction.AssetTransferTxn:
    """An opt-in is a zero-amount self-transfer. Nothing more."""
    return transaction.AssetTransferTxn(
        sender=address, sp=sp, receiver=address, amt=0, index=asa
    )


def build_session_open_group(
    sp,
    vault: Key,
    session: Key,
    asa: int,
    funding_micro: int,
    balance_micro: int,
) -> list[transaction.SignedTransaction]:
    """Fund, opt in, and load a session account in ONE atomic group, one round.

    Order is the whole trick. Algorand applies state changes between grouped
    transactions, so transaction 2 is signed by an account that transaction 1 has
    only just created, and transaction 3 transfers an asset into a slot that
    transaction 2 opened moments earlier. Attempting this as three separate
    submissions costs three rounds and can strand a half-built account.

    Atomicity is also the safety property: either the session exists funded and
    opted in, or nothing happened at all.
    """
    fund = transaction.PaymentTxn(
        sender=vault.address, sp=sp, receiver=session.address, amt=funding_micro
    )
    optin = build_optin(sp, session.address, asa)
    load = transaction.AssetTransferTxn(
        sender=vault.address, sp=sp, receiver=session.address, amt=balance_micro, index=asa
    )
    transaction.assign_group_id([fund, optin, load])
    return [
        fund.sign(vault.private_key),
        optin.sign(session.private_key),
        load.sign(vault.private_key),
    ]


def build_session_close_group(
    sp, vault_address: str, session: Key, asa: int, opted_in: bool = True
) -> list[transaction.SignedTransaction]:
    """Return everything to the vault and remove the session from chain state.

    ORDER MATTERS AND CANNOT BE SWAPPED. An account holding an ASA cannot be
    closed, so the asset close-out must come first; only then is the 0.1 ALGO slot
    minimum released and the account closable.

    `close_assets_to` moves the entire holding regardless of `amt`, and
    `close_remainder_to` moves the entire ALGO balance less this transaction's fee.
    Both are ordinary fields on ordinary transactions - which is precisely why
    DESIGN.md section 3 treats them as escape hatches when anything else signs.

    `opted_in=False` handles the account the reaper actually finds after a bad
    crash: funded but never opted in. Closing an asset it does not hold fails the
    whole group, so in that case there is only the ALGO close to do.
    """
    if not opted_in:
        close_only = transaction.PaymentTxn(
            sender=session.address,
            sp=sp,
            receiver=vault_address,
            amt=0,
            close_remainder_to=vault_address,
        )
        return [close_only.sign(session.private_key)]

    close_asset = transaction.AssetTransferTxn(
        sender=session.address,
        sp=sp,
        receiver=vault_address,
        amt=0,
        index=asa,
        close_assets_to=vault_address,
    )
    close_algo = transaction.PaymentTxn(
        sender=session.address,
        sp=sp,
        receiver=vault_address,
        amt=0,
        close_remainder_to=vault_address,
    )
    transaction.assign_group_id([close_asset, close_algo])
    return [
        close_asset.sign(session.private_key),
        close_algo.sign(session.private_key),
    ]


def submit(cli: algod.AlgodClient, signed: list, wait_rounds: int = 6) -> str:
    """Send a group and wait for confirmation. Returns the first txid."""
    txid = cli.send_transactions(signed)
    transaction.wait_for_confirmation(cli, txid, wait_rounds)
    return txid
