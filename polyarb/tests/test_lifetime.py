"""Tests for lifetime analysis using mock snapshot database."""

import sqlite3
from pathlib import Path

import pytest

from polyarb.analysis.costs import FeeParams
from polyarb.analysis.lifetime import (
    ArbWindow,
    PairLifetime,
    _find_windows,
    _seconds_between,
    analyze_pair,
    analyze_pairs,
    format_report,
    summary,
)
from polyarb.analysis.costs import ArbResult


# ── Unit tests ────────────────────────────────────────────────


def test_seconds_between():
    assert _seconds_between("2026-03-01T00:00:00Z", "2026-03-01T00:00:30Z") == 30
    assert _seconds_between("2026-03-01T00:00:00Z", "2026-03-01T01:00:00Z") == 3600
    assert _seconds_between("2026-03-01T23:59:30Z", "2026-03-02T00:00:00Z") == 30


def _arb(profit: float, direction: str = "poly_yes_kalshi_no") -> ArbResult:
    return ArbResult(
        direction=direction,
        poly_ask=0.40, kalshi_ask=0.50,
        gross_cost=0.90, poly_fee=0.01, kalshi_fee=0.02,
        net_profit=profit,
    )


def test_find_windows_single_run():
    scans = [
        ("2026-03-01T00:00:00Z", _arb(0.05)),
        ("2026-03-01T00:00:30Z", _arb(0.06)),
        ("2026-03-01T00:01:00Z", _arb(0.04)),
    ]
    windows = _find_windows(scans, scan_interval=30)
    assert len(windows) == 1
    assert windows[0].n_scans == 3
    assert windows[0].duration_seconds == 60
    assert windows[0].peak_profit == 0.06


def test_find_windows_gap_splits():
    scans = [
        ("2026-03-01T00:00:00Z", _arb(0.05)),
        ("2026-03-01T00:00:30Z", _arb(0.05)),
        # Gap — unprofitable scan
        ("2026-03-01T00:01:00Z", _arb(-0.01)),
        ("2026-03-01T00:01:30Z", _arb(-0.01)),
        # Second window
        ("2026-03-01T00:02:00Z", _arb(0.03)),
        ("2026-03-01T00:02:30Z", _arb(0.03)),
    ]
    windows = _find_windows(scans, scan_interval=30)
    assert len(windows) == 2
    assert windows[0].n_scans == 2
    assert windows[1].n_scans == 2


def test_find_windows_no_profitable_scans():
    scans = [
        ("2026-03-01T00:00:00Z", _arb(-0.01)),
        ("2026-03-01T00:00:30Z", _arb(-0.02)),
    ]
    windows = _find_windows(scans, scan_interval=30)
    assert len(windows) == 0


def test_find_windows_single_scan():
    scans = [
        ("2026-03-01T00:00:00Z", _arb(-0.01)),
        ("2026-03-01T00:00:30Z", _arb(0.05)),
        ("2026-03-01T00:01:00Z", _arb(-0.01)),
    ]
    windows = _find_windows(scans, scan_interval=30)
    assert len(windows) == 1
    assert windows[0].n_scans == 1
    assert windows[0].duration_seconds == 0  # single scan = 0 duration


def test_pair_lifetime_properties():
    lt = PairLifetime(
        poly_cid="0x1", kalshi_ticker="KX1",
        poly_question="Q1?", kalshi_question="Q2?",
        total_scans=100, profitable_scans=10,
        windows=[
            ArbWindow("t1", "t2", 300, 10, 0.05, 0.03, "poly_yes_kalshi_no"),
            ArbWindow("t3", "t4", 600, 20, 0.07, 0.04, "poly_yes_kalshi_no"),
        ],
    )
    assert lt.n_windows == 2
    assert lt.total_arb_seconds == 900
    assert lt.median_duration == 450.0
    assert lt.longest_window == 600
    assert lt.peak_profit == 0.07


def test_summary_no_arbs():
    lt = PairLifetime(
        poly_cid="0x1", kalshi_ticker="KX1",
        poly_question="Q?", kalshi_question="Q?",
        total_scans=100, profitable_scans=0,
    )
    stats = summary([lt])
    assert stats["pairs_with_arbs"] == 0
    assert stats["total_windows"] == 0
    assert stats["median_duration_seconds"] == 0


# ── Integration tests with mock DB ───────────────────────────

MOCK_DB = Path("mock_snapshots.db")

MATCHED_PAIRS = [
    ("0x0000aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "KXBTC150K-000"),
    ("0x0001aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "KXETH10K-001"),
    ("0x0002aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "KXFEDMAR26-002"),
    ("0x0003aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "KXTRUMP28-003"),
    ("0x0004aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "KXSPACEX26-004"),
    ("0x0005aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "KXGTA6-005"),
    ("0x0006aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "KXGDPQ2-006"),
    ("0x0007aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "KXAITURING-007"),
    ("0x0008aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "KXGOVSHUT26-008"),
    ("0x0009aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "KXSP6000-009"),
]


@pytest.fixture
def mock_db():
    if not MOCK_DB.exists():
        pytest.skip("mock_snapshots.db not found — run generate_mock_db.py first")
    return MOCK_DB


def test_profitable_pair_has_windows(mock_db):
    """BTC $150k pair should have at least one arb window."""
    lt = analyze_pair(mock_db, *MATCHED_PAIRS[0])
    assert lt.total_scans == 20_160
    assert lt.profitable_scans > 0
    assert lt.n_windows >= 1
    assert lt.longest_window > 0


def test_stable_pair_has_no_windows(mock_db):
    """Gov shutdown (stable) pair should have zero arb windows."""
    lt = analyze_pair(mock_db, *MATCHED_PAIRS[8])  # Gov shutdown
    assert lt.total_scans == 20_160
    assert lt.profitable_scans == 0
    assert lt.n_windows == 0


def test_false_positive_has_no_profitable_windows(mock_db):
    """GTA VI (false positive) pair — fees should eat the delta."""
    lt = analyze_pair(mock_db, *MATCHED_PAIRS[5])  # GTA VI
    # May have a few marginal scans but no sustained windows
    assert lt.profitable_scans < 100  # much less than 20,160


def test_analyze_all_pairs(mock_db):
    """Run analysis on all 10 matched pairs."""
    lifetimes = analyze_pairs(mock_db, MATCHED_PAIRS)
    assert len(lifetimes) == 10
    stats = summary(lifetimes)
    assert stats["pairs_analyzed"] == 10
    assert stats["pairs_with_arbs"] >= 1  # at least the profitable ones
    assert stats["total_windows"] >= 1


def test_window_durations_reasonable(mock_db):
    """Arb windows should last minutes to hours, not days."""
    lt = analyze_pair(mock_db, *MATCHED_PAIRS[0])  # BTC
    for w in lt.windows:
        assert w.duration_seconds <= 12 * 3600  # no window > 12 hours
        assert w.n_scans >= 1


def test_format_report_runs(mock_db):
    """format_report produces non-empty output."""
    lifetimes = analyze_pairs(mock_db, MATCHED_PAIRS)
    report = format_report(lifetimes)
    assert "LIFETIME ANALYSIS REPORT" in report
    assert "Median" in report


def test_nonexistent_pair(mock_db):
    """Unknown pair returns empty results."""
    lt = analyze_pair(mock_db, "0xNONEXISTENT", "KXFAKE-000")
    assert lt.total_scans == 0
    assert lt.profitable_scans == 0
    assert lt.n_windows == 0
