"""The session registry and spend counters - the file that stops money leaking.

Its one hard job: **a session address is written here before that session is
funded, and stays until the account is provably closed.** Every other ordering
loses money. Fund first and crash, and the ledger never learns the address; the
0.21 ALGO plus whatever balance it holds is stranded with nothing pointing at it.

Recovery needs only the index, because session keys are derived from the vault
seed (`keys.derive_session_key`) rather than generated. The ledger therefore holds
no secrets at all and could be world-readable without consequence - but it is
still written 0600, because the addresses it holds are a spending history.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

LEDGER_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SessionRecord:
    index: int
    address: str
    state: str                      # "opening" | "open" | "closed"
    opened_at: str
    balance_micro: int = 0
    funding_micro: int = 0
    open_txid: str | None = None
    closed_at: str | None = None
    close_txid: str | None = None
    spent_micro: int = 0

    @property
    def is_live(self) -> bool:
        """Anything not provably closed may still hold money.

        `opening` counts as live deliberately: it is the state a crash between
        writing the ledger and confirming the group leaves behind, and the group
        may well have landed.
        """
        return self.state in ("opening", "open")


@dataclass
class Ledger:
    path: Path
    version: int = LEDGER_VERSION
    next_index: int = 1
    sessions: list[SessionRecord] = field(default_factory=list)
    daily_spend: dict[str, int] = field(default_factory=dict)

    # ---- persistence -----------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> Ledger:
        if not path.exists():
            return cls(path=path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("version") != LEDGER_VERSION:
            raise SystemExit(
                f"Ledger at {path} is version {raw.get('version')}, expected "
                f"{LEDGER_VERSION}. Refusing to guess at its meaning while it may "
                "point at live accounts."
            )
        return cls(
            path=path,
            version=raw["version"],
            next_index=raw.get("next_index", 1),
            sessions=[SessionRecord(**s) for s in raw.get("sessions", [])],
            daily_spend=raw.get("daily_spend", {}),
        )

    def save(self) -> None:
        """Atomic replace, 0600, fsynced.

        The same reasoning as the vault seed: a torn write here is a ledger that
        cannot be parsed, and an unparseable ledger is an unreachable session
        account. Rename is atomic, so the file is either the old one or the new.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "version": self.version,
                "next_index": self.next_index,
                "sessions": [asdict(s) for s in self.sessions],
                "daily_spend": self.daily_spend,
            },
            indent=2,
        ).encode("utf-8")

        binary = getattr(os, "O_BINARY", 0)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | binary, 0o600)
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self.path)

    # ---- sessions --------------------------------------------------------

    def reserve_session(self, address: str, funding_micro: int, balance_micro: int) -> SessionRecord:
        """Record a session BEFORE it is funded, and persist immediately.

        The write happens here, not after submission, and that ordering is the
        entire point of this class.
        """
        record = SessionRecord(
            index=self.next_index,
            address=address,
            state="opening",
            opened_at=_utc_now(),
            balance_micro=balance_micro,
            funding_micro=funding_micro,
        )
        self.next_index += 1
        self.sessions.append(record)
        self.save()
        return record

    def get(self, index: int) -> SessionRecord | None:
        return next((s for s in self.sessions if s.index == index), None)

    def live_sessions(self) -> list[SessionRecord]:
        return [s for s in self.sessions if s.is_live]

    def mark_open(self, record: SessionRecord, txid: str) -> None:
        record.state = "open"
        record.open_txid = txid
        self.save()

    def mark_closed(self, record: SessionRecord, txid: str | None) -> None:
        record.state = "closed"
        record.close_txid = txid
        record.closed_at = _utc_now()
        self.save()

    # ---- spend counters --------------------------------------------------

    def today_key(self) -> str:
        return date.today().isoformat()

    def spent_today(self) -> int:
        return self.daily_spend.get(self.today_key(), 0)

    def record_spend(self, record: SessionRecord | None, micro: int) -> None:
        key = self.today_key()
        self.daily_spend[key] = self.daily_spend.get(key, 0) + micro
        if record is not None:
            record.spent_micro += micro
        self.save()
