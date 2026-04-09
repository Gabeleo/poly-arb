"""Tests for polyarb.db.models — SQLAlchemy Core table definitions."""

from __future__ import annotations

import pytest
from sqlalchemy import insert, text
from sqlalchemy.exc import IntegrityError

from polyarb.db.engine import create_engine
from polyarb.db.models import (
    execution_legs,
    executions,
    kalshi_snapshots,
    match_snapshots,
    metadata,
    polymarket_snapshots,
)


@pytest.fixture
def engine():
    """In-memory SQLite engine with all tables created."""
    eng = create_engine("sqlite:///:memory:")
    metadata.create_all(eng)
    yield eng
    eng.dispose()


def test_all_tables_in_metadata():
    """metadata.tables contains all 8 tables."""
    names = set(metadata.tables.keys())
    assert names == {
        "polymarket_snapshots",
        "kalshi_snapshots",
        "executions",
        "execution_legs",
        "match_snapshots",
        "audit_log",
        "positions",
        "risk_events",
    }


def test_polymarket_column_count():
    assert len(polymarket_snapshots.columns) == 12


def test_kalshi_column_count():
    assert len(kalshi_snapshots.columns) == 12


def test_executions_column_count():
    assert len(executions.columns) == 9


def test_execution_legs_column_count():
    assert len(execution_legs.columns) == 15


def test_match_snapshots_column_count():
    assert len(match_snapshots.columns) == 17


def test_create_all_succeeds(engine):
    """metadata.create_all on a fresh database creates all tables."""
    with engine.connect() as conn:
        tables = {
            r[0]
            for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }
    assert "polymarket_snapshots" in tables
    assert "kalshi_snapshots" in tables
    assert "executions" in tables
    assert "execution_legs" in tables
    assert "match_snapshots" in tables


def test_poly_unique_constraint(engine):
    """Duplicate (scan_ts, condition_id) is rejected."""
    row = {
        "scan_ts": "2026-04-06T12:00:00Z",
        "condition_id": "0xabc",
        "question": "Test?",
        "event_slug": "test",
        "yes_bid": 0.4,
        "yes_ask": 0.5,
        "no_bid": 0.5,
        "no_ask": 0.6,
        "volume": 10000,
        "volume_24h": 100,
        "end_date": None,
    }
    with engine.begin() as conn:
        conn.execute(insert(polymarket_snapshots), [row])
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(polymarket_snapshots), [row])


def test_kalshi_unique_constraint(engine):
    """Duplicate (scan_ts, ticker) is rejected."""
    row = {
        "scan_ts": "2026-04-06T12:00:00Z",
        "ticker": "KXBTC",
        "question": "Test?",
        "event_ticker": "KX",
        "yes_bid": 0.4,
        "yes_ask": 0.5,
        "no_bid": 0.5,
        "no_ask": 0.6,
        "volume": 10000,
        "volume_24h": 100,
        "close_time": None,
    }
    with engine.begin() as conn:
        conn.execute(insert(kalshi_snapshots), [row])
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(kalshi_snapshots), [row])


def test_match_snapshots_unique_constraint(engine):
    """Duplicate (scan_ts, poly_condition_id, kalshi_ticker) is rejected."""
    row = {
        "scan_ts": "2026-04-06T12:00:00Z",
        "scan_id": "abc123",
        "poly_condition_id": "0xabc",
        "kalshi_ticker": "KXBTC",
        "poly_question": "BTC?",
        "kalshi_question": "Bitcoin?",
        "confidence": 0.95,
        "poly_yes_bid": 0.4,
        "poly_yes_ask": 0.5,
        "poly_no_bid": 0.5,
        "poly_no_ask": 0.6,
        "kalshi_yes_bid": 0.4,
        "kalshi_yes_ask": 0.5,
        "kalshi_no_bid": 0.5,
        "kalshi_no_ask": 0.6,
        "raw_delta": 0.01,
    }
    with engine.begin() as conn:
        conn.execute(insert(match_snapshots), [row])
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(match_snapshots), [row])
