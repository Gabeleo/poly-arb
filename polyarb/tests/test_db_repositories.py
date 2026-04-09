"""Tests for polyarb.db.repositories — snapshot, execution, and match repos."""

from __future__ import annotations

import pytest

from polyarb.db.engine import create_engine
from polyarb.db.models import metadata
from polyarb.db.repositories.executions import SqliteExecutionRepository
from polyarb.db.repositories.matches import SqliteMatchSnapshotRepository
from polyarb.db.repositories.snapshots import SqliteSnapshotRepository


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    yield eng
    eng.dispose()


# ── Snapshot Repository ───────────────────────────────────────────


@pytest.fixture
def snap_repo(engine):
    return SqliteSnapshotRepository(engine)


def _poly_row(scan_ts="2026-04-06T12:00:00Z", cid="0xabc"):
    return {
        "scan_ts": scan_ts,
        "condition_id": cid,
        "question": "Will BTC hit 150k?",
        "event_slug": "btc-150k",
        "yes_bid": 0.42, "yes_ask": 0.44,
        "no_bid": 0.56, "no_ask": 0.58,
        "volume": 50000, "volume_24h": 1000,
        "end_date": "2026-06-30T00:00:00+00:00",
    }


def _kalshi_row(scan_ts="2026-04-06T12:00:00Z", ticker="KXBTC-150K"):
    return {
        "scan_ts": scan_ts,
        "ticker": ticker,
        "question": "Bitcoin above 150k?",
        "event_ticker": "KXBTC",
        "yes_bid": 0.39, "yes_ask": 0.43,
        "no_bid": 0.57, "no_ask": 0.61,
        "volume": 30000, "volume_24h": 500,
        "close_time": "2026-06-30T00:00:00+00:00",
    }


def test_insert_and_count_polymarket(snap_repo):
    ts = "2026-04-06T12:00:00Z"
    count = snap_repo.insert_polymarket(ts, [_poly_row(), _poly_row(cid="0xdef")])
    assert count == 2
    assert snap_repo.market_count()["polymarket"] == 2


def test_insert_and_count_kalshi(snap_repo):
    ts = "2026-04-06T12:00:00Z"
    count = snap_repo.insert_kalshi(ts, [_kalshi_row(), _kalshi_row(ticker="KXETH")])
    assert count == 2
    assert snap_repo.market_count()["kalshi"] == 2


def test_dedup_same_scan(snap_repo):
    ts = "2026-04-06T12:00:00Z"
    snap_repo.insert_polymarket(ts, [_poly_row()])
    count2 = snap_repo.insert_polymarket(ts, [_poly_row()])
    assert count2 == 0
    assert snap_repo.market_count()["polymarket"] == 1


def test_scan_count(snap_repo):
    snap_repo.insert_polymarket("2026-04-06T12:00:00Z", [_poly_row()])
    snap_repo.insert_polymarket("2026-04-06T12:00:30Z", [_poly_row(scan_ts="2026-04-06T12:00:30Z")])
    snap_repo.insert_kalshi("2026-04-06T12:00:00Z", [_kalshi_row()])
    counts = snap_repo.scan_count()
    assert counts["polymarket"] == 2
    assert counts["kalshi"] == 1


def test_get_pair_scans(snap_repo):
    ts = "2026-04-06T12:00:00Z"
    snap_repo.insert_polymarket(ts, [_poly_row(scan_ts=ts)])
    snap_repo.insert_kalshi(ts, [_kalshi_row(scan_ts=ts)])
    rows = snap_repo.get_pair_scans("0xabc", "KXBTC-150K")
    assert len(rows) == 1
    assert "poly_yes_ask" in rows[0]
    assert "kalshi_yes_ask" in rows[0]
    assert "poly_question" in rows[0]


def test_get_pair_scan_at(snap_repo):
    ts = "2026-04-06T12:00:00Z"
    snap_repo.insert_polymarket(ts, [_poly_row(scan_ts=ts)])
    snap_repo.insert_kalshi(ts, [_kalshi_row(scan_ts=ts)])

    row = snap_repo.get_pair_scan_at("0xabc", "KXBTC-150K", ts)
    assert row is not None
    assert row["poly_yes_ask"] == 0.44
    assert row["kalshi_yes_ask"] == 0.43

    # Non-existent pair
    assert snap_repo.get_pair_scan_at("0xabc", "NONEXIST", ts) is None


def test_get_distinct_scan_timestamps(snap_repo):
    snap_repo.insert_polymarket("2026-04-06T12:00:30Z", [_poly_row(scan_ts="2026-04-06T12:00:30Z")])
    snap_repo.insert_polymarket("2026-04-06T12:00:00Z", [_poly_row(scan_ts="2026-04-06T12:00:00Z")])
    ts_list = snap_repo.get_distinct_scan_timestamps()
    assert ts_list == ["2026-04-06T12:00:00Z", "2026-04-06T12:00:30Z"]


# ── Execution Repository ─────────────────────────────────────────


@pytest.fixture
def exec_repo(engine):
    return SqliteExecutionRepository(engine)


def test_full_lifecycle(exec_repo):
    exec_repo.record_execution("exec-1", "match-key-1", 1)
    row_id = exec_repo.record_attempt(
        "exec-1", 0, "kalshi", "TICKER-1", "yes", "buy", 0.42, 10.0,
    )
    exec_repo.mark_sent(row_id)
    exec_repo.record_result(row_id, "order-abc", "filled", fill_qty=10.0)
    exec_repo.record_completion("exec-1", True, 0.05)

    history = exec_repo.get_history(limit=1)
    assert len(history) == 1
    assert history[0]["status"] == "completed"
    assert history[0]["profit"] == 0.05
    assert len(history[0]["legs"]) == 1
    assert history[0]["legs"][0]["status"] == "filled"


def test_orphan_detection(exec_repo):
    exec_repo.record_execution("exec-2", "mk-2", 2)
    r1 = exec_repo.record_attempt("exec-2", 0, "kalshi", "T-1", "yes", "buy", 0.40, 10.0)
    exec_repo.mark_sent(r1)
    r2 = exec_repo.record_attempt("exec-2", 1, "polymarket", "T-2", "no", "buy", 0.35, 10.0)
    exec_repo.mark_sent(r2)
    exec_repo.record_result(r1, "k-1", "filled")

    orphans = exec_repo.get_orphans()
    assert len(orphans) == 1
    assert orphans[0]["id"] == r2


def test_history_with_legs(exec_repo):
    exec_repo.record_execution("exec-3", "mk-3", 2)
    exec_repo.record_attempt("exec-3", 0, "kalshi", "T-1", "yes", "buy", 0.40, 10.0)
    exec_repo.record_attempt("exec-3", 1, "poly", "T-2", "no", "buy", 0.35, 10.0)

    history = exec_repo.get_history()
    assert len(history) == 1
    assert len(history[0]["legs"]) == 2


def test_duplicate_execution_id_rejected(exec_repo):
    exec_repo.record_execution("exec-dup", "mk-dup", 1)
    with pytest.raises(Exception):
        exec_repo.record_execution("exec-dup", "mk-dup", 1)


def test_count_by_status(exec_repo):
    exec_repo.record_execution("exec-4", "mk-4", 2)
    r1 = exec_repo.record_attempt("exec-4", 0, "kalshi", "T-1", "yes", "buy", 0.40, 10.0)
    r2 = exec_repo.record_attempt("exec-4", 1, "poly", "T-2", "no", "buy", 0.35, 10.0)
    exec_repo.mark_sent(r1)
    exec_repo.mark_sent(r2)
    exec_repo.record_result(r1, "k-1", "filled")

    assert exec_repo.count_by_status("sent") == 1
    assert exec_repo.count_by_status("filled") == 1
    assert exec_repo.count_by_status("pending") == 0


def test_mark_orphaned(exec_repo):
    exec_repo.record_execution("exec-5", "mk-5", 1)
    r = exec_repo.record_attempt("exec-5", 0, "poly", "T-1", "no", "buy", 0.50, 5.0)
    exec_repo.mark_sent(r)
    assert len(exec_repo.get_orphans()) == 1

    exec_repo.mark_orphaned(r)
    assert exec_repo.get_orphans() == []
    assert exec_repo.count_by_status("orphaned") == 1


# ── Match Snapshot Repository ────────────────────────────────────


@pytest.fixture
def match_repo(engine):
    return SqliteMatchSnapshotRepository(engine)


def _match_dict(cid="0xabc", ticker="KXBTC-150K"):
    return {
        "poly_condition_id": cid,
        "kalshi_ticker": ticker,
        "poly_question": "Will BTC hit 150k?",
        "kalshi_question": "Bitcoin above 150k?",
        "confidence": 0.92,
        "poly_yes_bid": 0.40, "poly_yes_ask": 0.44,
        "poly_no_bid": 0.56, "poly_no_ask": 0.60,
        "kalshi_yes_bid": 0.38, "kalshi_yes_ask": 0.42,
        "kalshi_no_bid": 0.58, "kalshi_no_ask": 0.62,
    }


def test_insert_and_retrieve(match_repo):
    count = match_repo.insert_matches(
        "2026-04-06T12:00:00Z", "scan-1",
        [_match_dict(), _match_dict(cid="0xdef")],
    )
    assert count == 2

    history = match_repo.get_pair_history("0xabc", "KXBTC-150K")
    assert len(history) == 1
    assert history[0]["confidence"] == 0.92


def test_get_recorded_pairs(match_repo):
    match_repo.insert_matches(
        "2026-04-06T12:00:00Z", "scan-1",
        [_match_dict(), _match_dict(cid="0xdef", ticker="KXETH")],
    )
    pairs = match_repo.get_recorded_pairs()
    assert len(pairs) == 2
    assert ("0xabc", "KXBTC-150K") in pairs
    assert ("0xdef", "KXETH") in pairs


def test_dedup_match_same_scan_pair(match_repo):
    """Same (scan_ts, poly_condition_id, kalshi_ticker) is deduplicated."""
    match_repo.insert_matches("2026-04-06T12:00:00Z", "scan-1", [_match_dict()])
    count = match_repo.insert_matches("2026-04-06T12:00:00Z", "scan-1", [_match_dict()])
    assert count == 1  # OR IGNORE returns len(rows) but row is ignored

    history = match_repo.get_pair_history("0xabc", "KXBTC-150K")
    assert len(history) == 1


def test_raw_delta_computed(match_repo):
    """raw_delta is computed from poly_yes_ask and kalshi_no_bid."""
    match_repo.insert_matches("2026-04-06T12:00:00Z", "scan-1", [_match_dict()])
    history = match_repo.get_pair_history("0xabc", "KXBTC-150K")
    assert history[0]["raw_delta"] > 0


def test_get_scan_matches(match_repo):
    match_repo.insert_matches(
        "2026-04-06T12:00:00Z", "scan-1",
        [_match_dict(), _match_dict(cid="0xdef")],
    )
    matches = match_repo.get_scan_matches("2026-04-06T12:00:00Z")
    assert len(matches) == 2


def test_empty_insert(match_repo):
    count = match_repo.insert_matches("2026-04-06T12:00:00Z", "scan-1", [])
    assert count == 0
