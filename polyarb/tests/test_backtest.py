"""Tests for backtest engine."""

import sqlite3
from pathlib import Path

import pytest

from polyarb.analysis.backtest import (
    BacktestResult,
    Trade,
    run_backtest,
    format_report,
)
from polyarb.analysis.costs import FeeParams


# ── Unit tests on BacktestResult properties ───────────────────


def _trade(profit: float, cost: float = 0.95, days: float = 90.0) -> Trade:
    return Trade(
        entry_ts="2026-03-01T00:00:00Z",
        poly_cid="0x1", kalshi_ticker="KX1",
        direction="poly_yes_kalshi_no",
        poly_ask=0.40, kalshi_ask=0.50,
        gross_cost=0.90, poly_fee=0.02, kalshi_fee=0.02,
        total_cost=cost, net_profit=profit,
        settlement_date="2026-06-30T00:00:00+00:00",
        days_to_settlement=days,
    )


def test_empty_result():
    r = BacktestResult()
    assert r.n_trades == 0
    assert r.total_profit == 0.0
    assert r.avg_profit == 0.0
    assert r.max_capital_deployed == 0.0
    assert r.max_drawdown == 0.0


def test_result_properties():
    r = BacktestResult(
        trades=[_trade(0.05, 0.95), _trade(0.03, 0.97)],
        capital_curve=[("t1", 0.95), ("t2", 1.92)],
    )
    assert r.n_trades == 2
    assert abs(r.total_profit - 0.08) < 1e-9
    assert abs(r.total_cost - 1.92) < 1e-9
    assert abs(r.avg_profit - 0.04) < 1e-9
    assert r.max_capital_deployed == 1.92


def test_max_drawdown_no_drawdown():
    """All profitable trades → no drawdown."""
    r = BacktestResult(trades=[_trade(0.05), _trade(0.03)])
    assert r.max_drawdown == 0.0


def test_max_drawdown_with_loss():
    """A loss creates drawdown."""
    r = BacktestResult(trades=[_trade(0.05), _trade(-0.02), _trade(0.01)])
    # Cumulative: 0.05, 0.03, 0.04
    # Peak: 0.05
    # After loss: 0.03 → drawdown = 0.03 - 0.05 = -0.02
    assert abs(r.max_drawdown - (-0.02)) < 1e-9


# ── Inline DB tests ──────────────────────────────────────────


def _create_test_db(tmp_path, poly_rows, kalshi_rows):
    """Create a minimal test DB with given rows."""
    from polyarb.recorder.db import SCHEMA
    db_path = tmp_path / "test_bt.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.executemany(
        """INSERT INTO polymarket_snapshots
           (scan_ts, condition_id, question, event_slug,
            yes_bid, yes_ask, no_bid, no_ask,
            volume, volume_24h, end_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        poly_rows,
    )
    conn.executemany(
        """INSERT INTO kalshi_snapshots
           (scan_ts, ticker, question, event_ticker,
            yes_bid, yes_ask, no_bid, no_ask,
            volume, volume_24h, close_time)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        kalshi_rows,
    )
    conn.commit()
    conn.close()
    return db_path


def test_single_arb_window(tmp_path):
    """One pair, 3 profitable scans → 1 trade entered on first scan."""
    fees = FeeParams(poly_fee_rate=0.0, kalshi_fee_cap=0.0)  # zero fees for clarity

    # YES on poly cheap (0.40), NO on kalshi cheap (0.50) → cost 0.90 → profit 0.10
    poly = [
        ("2026-03-01T00:00:00Z", "0xA", "Q?", "slug", 0.39, 0.40, 0.60, 0.61, 50000, 1000, "2026-06-30T00:00:00+00:00"),
        ("2026-03-01T00:00:30Z", "0xA", "Q?", "slug", 0.39, 0.40, 0.60, 0.61, 50000, 1000, "2026-06-30T00:00:00+00:00"),
        ("2026-03-01T00:01:00Z", "0xA", "Q?", "slug", 0.39, 0.40, 0.60, 0.61, 50000, 1000, "2026-06-30T00:00:00+00:00"),
    ]
    kalshi = [
        ("2026-03-01T00:00:00Z", "KX1", "Q?", "EVT", 0.52, 0.54, 0.48, 0.50, 30000, 500, "2026-06-30T00:00:00+00:00"),
        ("2026-03-01T00:00:30Z", "KX1", "Q?", "EVT", 0.52, 0.54, 0.48, 0.50, 30000, 500, "2026-06-30T00:00:00+00:00"),
        ("2026-03-01T00:01:00Z", "KX1", "Q?", "EVT", 0.52, 0.54, 0.48, 0.50, 30000, 500, "2026-06-30T00:00:00+00:00"),
    ]

    db_path = _create_test_db(tmp_path, poly, kalshi)
    result = run_backtest(db_path, [("0xA", "KX1")], fees=fees)

    assert result.n_trades == 1  # entered once, not 3 times
    assert result.trades[0].entry_ts == "2026-03-01T00:00:00Z"
    assert result.trades[0].net_profit > 0


def test_two_windows_two_trades(tmp_path):
    """Profitable, then not, then profitable again → 2 trades."""
    fees = FeeParams(poly_fee_rate=0.0, kalshi_fee_cap=0.0)

    poly = [
        ("2026-03-01T00:00:00Z", "0xA", "Q?", "s", 0.39, 0.40, 0.60, 0.61, 50000, 1000, "2026-06-30T00:00:00+00:00"),
        ("2026-03-01T00:00:30Z", "0xA", "Q?", "s", 0.49, 0.50, 0.50, 0.51, 50000, 1000, "2026-06-30T00:00:00+00:00"),  # no arb
        ("2026-03-01T00:01:00Z", "0xA", "Q?", "s", 0.39, 0.40, 0.60, 0.61, 50000, 1000, "2026-06-30T00:00:00+00:00"),
    ]
    kalshi = [
        ("2026-03-01T00:00:00Z", "KX1", "Q?", "E", 0.52, 0.54, 0.48, 0.50, 30000, 500, "2026-06-30T00:00:00+00:00"),
        ("2026-03-01T00:00:30Z", "KX1", "Q?", "E", 0.52, 0.54, 0.48, 0.50, 30000, 500, "2026-06-30T00:00:00+00:00"),
        ("2026-03-01T00:01:00Z", "KX1", "Q?", "E", 0.52, 0.54, 0.48, 0.50, 30000, 500, "2026-06-30T00:00:00+00:00"),
    ]

    db_path = _create_test_db(tmp_path, poly, kalshi)
    result = run_backtest(db_path, [("0xA", "KX1")], fees=fees)

    assert result.n_trades == 2


def test_no_arb_no_trades(tmp_path):
    """Balanced prices → no trades."""
    poly = [
        ("2026-03-01T00:00:00Z", "0xA", "Q?", "s", 0.49, 0.51, 0.49, 0.51, 50000, 1000, "2026-06-30T00:00:00+00:00"),
    ]
    kalshi = [
        ("2026-03-01T00:00:00Z", "KX1", "Q?", "E", 0.49, 0.51, 0.49, 0.51, 30000, 500, "2026-06-30T00:00:00+00:00"),
    ]

    db_path = _create_test_db(tmp_path, poly, kalshi)
    result = run_backtest(db_path, [("0xA", "KX1")])

    assert result.n_trades == 0
    assert result.total_profit == 0.0


def test_capital_tracking(tmp_path):
    """Capital curve reflects trade entry."""
    fees = FeeParams(poly_fee_rate=0.0, kalshi_fee_cap=0.0)

    poly = [
        ("2026-03-01T00:00:00Z", "0xA", "Q?", "s", 0.39, 0.40, 0.60, 0.61, 50000, 1000, "2026-06-30T00:00:00+00:00"),
        ("2026-03-01T00:00:30Z", "0xA", "Q?", "s", 0.39, 0.40, 0.60, 0.61, 50000, 1000, "2026-06-30T00:00:00+00:00"),
    ]
    kalshi = [
        ("2026-03-01T00:00:00Z", "KX1", "Q?", "E", 0.52, 0.54, 0.48, 0.50, 30000, 500, "2026-06-30T00:00:00+00:00"),
        ("2026-03-01T00:00:30Z", "KX1", "Q?", "E", 0.52, 0.54, 0.48, 0.50, 30000, 500, "2026-06-30T00:00:00+00:00"),
    ]

    db_path = _create_test_db(tmp_path, poly, kalshi)
    result = run_backtest(db_path, [("0xA", "KX1")], fees=fees)

    # First scan: no capital yet (trade enters during this scan)
    # Second scan: capital from the trade is in use
    assert result.max_capital_deployed > 0
    assert len(result.capital_curve) == 2


def test_days_to_settlement(tmp_path):
    """days_to_settlement computed correctly."""
    fees = FeeParams(poly_fee_rate=0.0, kalshi_fee_cap=0.0)

    poly = [
        ("2026-03-01T00:00:00Z", "0xA", "Q?", "s", 0.39, 0.40, 0.60, 0.61, 50000, 1000, "2026-03-08T00:00:00+00:00"),
    ]
    kalshi = [
        ("2026-03-01T00:00:00Z", "KX1", "Q?", "E", 0.52, 0.54, 0.48, 0.50, 30000, 500, "2026-03-08T00:00:00+00:00"),
    ]

    db_path = _create_test_db(tmp_path, poly, kalshi)
    result = run_backtest(db_path, [("0xA", "KX1")], fees=fees)

    assert result.n_trades == 1
    assert result.trades[0].days_to_settlement == 7.0


# ── Integration test with mock DB ────────────────────────────

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


def test_backtest_mock_profitable_pairs_only(mock_db):
    """Only profitable pairs should produce trades."""
    profitable_pairs = MATCHED_PAIRS[:5]
    result = run_backtest(mock_db, profitable_pairs)
    assert result.n_trades >= 1
    assert result.total_profit > 0


def test_backtest_mock_stable_pairs_no_trades(mock_db):
    """Stable pairs should produce zero trades."""
    stable_pairs = MATCHED_PAIRS[8:10]
    result = run_backtest(mock_db, stable_pairs)
    assert result.n_trades == 0


def test_backtest_mock_false_positives_no_trades(mock_db):
    """False positive pairs should produce zero trades (fees eat delta)."""
    fp_pairs = MATCHED_PAIRS[5:8]
    result = run_backtest(mock_db, fp_pairs)
    assert result.n_trades == 0


def test_backtest_mock_full_report(mock_db):
    """Full backtest produces a report."""
    result = run_backtest(mock_db, MATCHED_PAIRS)
    report = format_report(result)
    assert "BACKTEST REPORT" in report
    assert "Total P&L" in report


def test_backtest_mock_all_profits_positive(mock_db):
    """Every entered trade should have positive net profit (arb = locked profit)."""
    result = run_backtest(mock_db, MATCHED_PAIRS)
    for t in result.trades:
        assert t.net_profit > 0, f"Trade at {t.entry_ts} has negative profit: {t.net_profit}"
