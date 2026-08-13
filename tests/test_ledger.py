"""The ledger's state machine, tested without a network.

The property everything else rests on: a session address reaches disk BEFORE the
funding group is submitted. If that ordering ever inverts, a crash in the gap
leaves a funded account with nothing on disk pointing at it, and the money is
gone with no way to even know it existed.
"""

from __future__ import annotations

import json

import pytest

from obol.errors import WalletError
from obol.ledger import LEDGER_VERSION, Ledger, SessionRecord


def test_missing_ledger_starts_empty(tmp_path):
    ledger = Ledger.load(tmp_path / "ledger.json")
    assert ledger.sessions == []
    assert ledger.next_index == 1


def test_reserve_persists_immediately(tmp_path):
    """The ordering invariant, asserted against the file rather than the object."""
    path = tmp_path / "ledger.json"
    ledger = Ledger.load(path)
    record = ledger.reserve_session("ADDR-1", 210_000, 1_000_000)

    assert path.exists(), "reserve_session must write before returning"
    on_disk = json.loads(path.read_text())
    assert on_disk["sessions"][0]["address"] == "ADDR-1"
    assert on_disk["sessions"][0]["state"] == "opening"
    assert record.index == 1
    assert ledger.next_index == 2


def test_indexes_never_repeat_even_across_reloads(tmp_path):
    """A repeated index would derive a session key already used and closed."""
    path = tmp_path / "ledger.json"
    seen = set()
    for _ in range(5):
        ledger = Ledger.load(path)
        record = ledger.reserve_session(f"ADDR-{ledger.next_index}", 210_000, 0)
        assert record.index not in seen
        seen.add(record.index)
    assert seen == {1, 2, 3, 4, 5}


def test_opening_counts_as_live(tmp_path):
    """The crash-between-write-and-confirm state must be swept, not skipped."""
    ledger = Ledger.load(tmp_path / "ledger.json")
    record = ledger.reserve_session("ADDR-1", 210_000, 1_000_000)
    assert record.state == "opening"
    assert record.is_live
    assert ledger.live_sessions() == [record]


def test_lifecycle_transitions(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = Ledger.load(path)
    record = ledger.reserve_session("ADDR-1", 210_000, 1_000_000)

    ledger.mark_open(record, "TXID-OPEN")
    assert Ledger.load(path).live_sessions()[0].state == "open"

    ledger.mark_closed(record, "TXID-CLOSE")
    reloaded = Ledger.load(path)
    assert reloaded.live_sessions() == []
    assert reloaded.sessions[0].state == "closed"
    assert reloaded.sessions[0].close_txid == "TXID-CLOSE"
    assert reloaded.sessions[0].closed_at is not None


def test_round_trips_through_disk(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = Ledger.load(path)
    first = ledger.reserve_session("ADDR-1", 210_000, 1_000_000)
    ledger.mark_open(first, "TXID-1")
    ledger.record_spend(first, 50_000)

    reloaded = Ledger.load(path)
    assert [s.address for s in reloaded.sessions] == ["ADDR-1"]
    assert reloaded.sessions[0].spent_micro == 50_000
    assert reloaded.spent_today() == 50_000


def test_spend_accumulates_per_day(tmp_path):
    ledger = Ledger.load(tmp_path / "ledger.json")
    record = ledger.reserve_session("ADDR-1", 210_000, 1_000_000)
    ledger.record_spend(record, 50_000)
    ledger.record_spend(record, 25_000)
    assert ledger.spent_today() == 75_000
    assert record.spent_micro == 75_000

    # A counter from another day must not leak into today's total.
    ledger.daily_spend["2020-01-01"] = 999_000
    assert ledger.spent_today() == 75_000


def test_spend_without_a_session_still_counts(tmp_path):
    ledger = Ledger.load(tmp_path / "ledger.json")
    ledger.record_spend(None, 10_000)
    assert ledger.spent_today() == 10_000


def test_refuses_an_unknown_version(tmp_path):
    """A ledger we cannot interpret may still point at funded accounts."""
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"version": LEDGER_VERSION + 1, "sessions": []}))
    with pytest.raises(WalletError):
        Ledger.load(path)


def test_save_is_atomic_and_leaves_no_temp(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = Ledger.load(path)
    ledger.reserve_session("ADDR-1", 210_000, 1_000_000)
    assert not path.with_suffix(".tmp").exists()
    assert json.loads(path.read_text())["version"] == LEDGER_VERSION


def test_get_returns_none_for_unknown_index(tmp_path):
    ledger = Ledger.load(tmp_path / "ledger.json")
    assert ledger.get(42) is None


def test_record_defaults_are_live():
    record = SessionRecord(index=1, address="A", state="opening", opened_at="now")
    assert record.is_live
    assert record.spent_micro == 0
