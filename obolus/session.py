"""Session lifecycle: bootstrap the vault, open, close, and reap what crashed.

The money-losing bugs in this project live in this file. Two invariants carry the
weight:

1. **The ledger is written before the chain.** `reserve_session` persists the
   address before a single microALGO moves, so a crash at any instant afterwards
   leaves a record the reaper can act on.
2. **The reaper assumes nothing about how it died.** It re-reads chain state for
   every live record rather than trusting the ledger's own `state` field, because
   the interesting failures are exactly the ones where those two disagree.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import WalletError
from . import algorand
from .config import SESSION_FUNDING_MICRO, VAULT_MIN_ALGO_MICRO, Config
from .keys import Key, derive_session_key, load_or_create_vault
from .ledger import Ledger, SessionRecord


@dataclass(frozen=True)
class VaultStatus:
    """Where the vault is in the three-step bootstrap of DESIGN.md section 3.1."""

    address: str
    created: bool
    algo_micro: int
    opted_in: bool
    asset_micro: int
    step: int          # 1 send ALGO, 2 opt in, 3 send USDC, 0 ready
    message: str

    @property
    def ready(self) -> bool:
        return self.step == 0


def open_vault(cfg: Config) -> tuple[Key, bool]:
    return load_or_create_vault(cfg.seed_path)


def vault_status(cfg: Config) -> VaultStatus:
    """Read the vault and say which bootstrap step the human is on.

    Reported every time, not only on failure. The failure this prevents is a user
    sending USDC to a vault that has not opted in, watching it bounce, and having
    no idea why - the transfer is rejected outright, there is no pending state.
    """
    vault, created = open_vault(cfg)
    cli = algorand.client(cfg.network)
    state = algorand.account_state(cli, vault.address, cfg.network.payment_asa)
    asset = cfg.network.payment_asa

    if state.algo_micro < VAULT_MIN_ALGO_MICRO:
        step, message = 1, (
            f"Send at least {VAULT_MIN_ALGO_MICRO / 1e6:.2f} ALGO to the vault. It "
            "cannot pay the fee for its own opt-in without this, and 0.1 of it is "
            "locked as the asset slot minimum."
        )
    elif not state.opted_in:
        step, message = 2, (
            f"Opt the vault into ASA {asset}: run `obol vault optin`. Until then "
            "any USDC sent here is REJECTED, not held."
        )
    elif state.asset_micro == 0:
        step, message = 3, (
            f"Send USDC (ASA {asset}) to the vault. The opt-in is done, so it will "
            "arrive."
        )
    else:
        step, message = 0, "Vault is funded and ready."

    return VaultStatus(
        address=vault.address,
        created=created,
        algo_micro=state.algo_micro,
        opted_in=state.opted_in,
        asset_micro=state.asset_micro,
        step=step,
        message=message,
    )


def vault_optin(cfg: Config) -> str:
    """Step 2 of the bootstrap - the one transaction the vault signs alone.

    Every other vault signature is part of a session funding group.
    """
    vault, _ = open_vault(cfg)
    cli = algorand.client(cfg.network)
    state = algorand.account_state(cli, vault.address, cfg.network.payment_asa)
    if state.opted_in:
        raise WalletError(f"Vault is already opted into ASA {cfg.network.payment_asa}.")
    if state.algo_micro < VAULT_MIN_ALGO_MICRO:
        raise WalletError(
            f"Vault holds {state.algo_micro / 1e6:.6f} ALGO; needs at least "
            f"{VAULT_MIN_ALGO_MICRO / 1e6:.2f} to opt in. This is step 1."
        )
    sp = algorand.suggested_params(cli)
    txn = algorand.build_optin(sp, vault.address, cfg.network.payment_asa)
    return algorand.submit(cli, [txn.sign(vault.private_key)])


def open_session(cfg: Config, balance_micro: int) -> tuple[SessionRecord, str]:
    """Create, fund and opt in a session account in one atomic group."""
    vault, _ = open_vault(cfg)
    ledger = Ledger.load(cfg.ledger_path)

    # Decision 6: one session per vault, serialised. It removes the nonce and
    # ordering problem on vault-signed funding groups rather than solving it.
    if live := ledger.live_sessions():
        raise WalletError(
            f"Session {live[0].index} ({live[0].address}) is still live. Close it "
            "or run `obol reap` first - v1 runs one session at a time."
        )

    cli = algorand.client(cfg.network)
    asset = cfg.network.payment_asa
    state = algorand.account_state(cli, vault.address, asset)
    if not state.opted_in:
        raise WalletError(
            f"Vault is not opted into ASA {asset}. Run `obol vault` for the "
            "bootstrap steps - a session cannot return its balance to a vault that "
            "cannot receive it."
        )
    if state.asset_micro < balance_micro:
        raise WalletError(
            f"Vault holds {cfg.network.fmt(state.asset_micro)} of ASA {asset}, "
            f"needs {cfg.network.fmt(balance_micro)}."
        )
    # Two vault-signed fees plus the funding itself.
    needed_algo = SESSION_FUNDING_MICRO + 2 * algorand.MIN_FEE_MICRO
    if state.spendable_algo_micro < needed_algo:
        raise WalletError(
            f"Vault has {state.spendable_algo_micro / 1e6:.6f} ALGO above its "
            f"minimum balance; opening a session needs {needed_algo / 1e6:.6f}."
        )

    session = derive_session_key(vault.seed, ledger.next_index)

    # THE ORDERING THAT MATTERS: persist before funding. A crash after this line
    # is recoverable; a crash before the equivalent line would not be.
    record = ledger.reserve_session(
        session.address, SESSION_FUNDING_MICRO, balance_micro
    )

    sp = algorand.suggested_params(cli)
    group = algorand.build_session_open_group(
        sp, vault, session, asset, SESSION_FUNDING_MICRO, balance_micro
    )
    txid = algorand.submit(cli, group)
    ledger.mark_open(record, txid)
    return record, txid


def live_session(cfg: Config) -> tuple[Key, SessionRecord, Ledger]:
    """The open session's key, its record, and the ledger holding both.

    Refuses rather than opening one silently: a spend path that provisions its own
    funding source on demand is not something a user can reason about. The MCP
    server opens lazily on the first paid call (Phase 4), where the human has
    already approved a session budget; the CLI stays explicit.
    """
    vault, _ = open_vault(cfg)
    ledger = Ledger.load(cfg.ledger_path)
    live = [s for s in ledger.live_sessions() if s.state == "open"]
    if not live:
        raise WalletError(
            "No open session. Run `obol session open --balance <amount>` first.\n"
            "A session is what bounds the loss: payments spend from it, never from "
            "the vault."
        )
    record = live[0]
    key = derive_session_key(vault.seed, record.index)
    if key.address != record.address:
        raise WalletError(
            f"Session {record.index} in the ledger is {record.address}, but this "
            f"vault derives {key.address}. Refusing to sign for another vault's "
            "session."
        )
    return key, record, ledger


def close_session(cfg: Config, index: int | None = None) -> tuple[SessionRecord, str | None]:
    """Close a session and sweep everything back to the vault."""
    vault, _ = open_vault(cfg)
    ledger = Ledger.load(cfg.ledger_path)

    if index is None:
        live = ledger.live_sessions()
        if not live:
            raise WalletError("No live session to close.")
        record = live[0]
    else:
        record = ledger.get(index)
        if record is None:
            raise WalletError(f"No session with index {index} in the ledger.")

    txid = _sweep(cfg, vault, ledger, record)
    return record, txid


def reap(cfg: Config) -> list[tuple[SessionRecord, str | None, str]]:
    """Sweep every session the ledger still believes is live.

    This is the function that makes an unclean exit cost nothing, and the one whose
    absence would leak 0.21 ALGO plus the session balance on every crash, silently.

    It trusts the chain over the ledger: a record marked `open` whose account no
    longer exists was already swept, and a record marked `opening` may well have a
    funded account behind it.
    """
    vault, _ = open_vault(cfg)
    ledger = Ledger.load(cfg.ledger_path)
    results: list[tuple[SessionRecord, str | None, str]] = []

    for record in ledger.live_sessions():
        try:
            txid = _sweep(cfg, vault, ledger, record)
            results.append((record, txid, "swept" if txid else "already gone"))
        except Exception as exc:  # one bad record must not strand the rest
            results.append((record, None, f"FAILED: {str(exc)[:160]}"))
    return results


def _sweep(cfg: Config, vault: Key, ledger: Ledger, record: SessionRecord) -> str | None:
    """Close one session account. Returns the txid, or None if nothing was there.

    The session key is re-derived from the vault seed rather than recalled from
    memory, which is exactly why this works after a crash.
    """
    session = derive_session_key(vault.seed, record.index)
    if session.address != record.address:
        # Derivation is deterministic, so a mismatch means the ledger belongs to a
        # different vault. Sweeping would sign for an account we cannot control.
        raise WalletError(
            f"Session {record.index} in the ledger is {record.address}, but this "
            f"vault derives {session.address}. Refusing to act on another vault's "
            "ledger."
        )

    cli = algorand.client(cfg.network)
    state = algorand.account_state(cli, session.address, cfg.network.payment_asa)
    if state.nothing_to_recover:
        # Never funded, or already closed. Either way there is nothing here.
        #
        # `exists` alone is NOT the test, and assuming it was is a bug this file
        # carried until a real close-out proved otherwise: algod keeps answering for
        # a closed account, returning a zeroed record rather than a 404. Sweeping on
        # `exists` would build a close group against an empty account, which fails
        # for the fee - and the reaper would report failure on every session it had
        # already successfully closed.
        ledger.mark_closed(record, None)
        return None

    sp = algorand.suggested_params(cli)
    group = algorand.build_session_close_group(
        sp, vault.address, session, cfg.network.payment_asa, opted_in=state.opted_in
    )
    txid = algorand.submit(cli, group)
    ledger.mark_closed(record, txid)
    return txid
