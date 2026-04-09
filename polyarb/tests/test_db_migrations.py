"""Tests for Alembic migrations."""

from __future__ import annotations

import sqlite3

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import inspect, text

from polyarb.db.engine import create_engine


def _alembic_config(db_path: str) -> AlembicConfig:
    """Create Alembic config pointing at a specific database."""
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", "polyarb/db/migrations")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "migration_test.db")


def test_upgrade_to_head(db_path):
    """alembic upgrade head on a fresh database creates all tables."""
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "polymarket_snapshots" in tables
    assert "kalshi_snapshots" in tables
    assert "executions" in tables
    assert "execution_legs" in tables
    assert "match_snapshots" in tables
    assert "audit_log" in tables
    assert "positions" in tables
    assert "risk_events" in tables
    assert "alembic_version" in tables
    engine.dispose()


def test_downgrade_one_step(db_path):
    """Downgrade -1 drops positions and risk_events but keeps other tables."""
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")

    engine = create_engine(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "positions" not in tables
    assert "risk_events" not in tables
    assert "audit_log" in tables
    assert "match_snapshots" in tables
    assert "polymarket_snapshots" in tables
    assert "executions" in tables
    engine.dispose()


def test_idempotent_on_existing_schema(db_path):
    """Upgrade is safe on a database with tables created by old inline SQL."""
    # Simulate the old RecorderDB creating tables directly
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS polymarket_snapshots (
            id            INTEGER PRIMARY KEY,
            scan_ts       TEXT NOT NULL,
            condition_id  TEXT NOT NULL,
            question      TEXT NOT NULL,
            event_slug    TEXT NOT NULL DEFAULT '',
            yes_bid       REAL NOT NULL,
            yes_ask       REAL NOT NULL,
            no_bid        REAL NOT NULL,
            no_ask        REAL NOT NULL,
            volume        REAL NOT NULL,
            volume_24h    REAL NOT NULL DEFAULT 0,
            end_date      TEXT,
            UNIQUE(scan_ts, condition_id)
        );
        CREATE TABLE IF NOT EXISTS kalshi_snapshots (
            id            INTEGER PRIMARY KEY,
            scan_ts       TEXT NOT NULL,
            ticker        TEXT NOT NULL,
            question      TEXT NOT NULL,
            event_ticker  TEXT NOT NULL DEFAULT '',
            yes_bid       REAL NOT NULL,
            yes_ask       REAL NOT NULL,
            no_bid        REAL NOT NULL,
            no_ask        REAL NOT NULL,
            volume        REAL NOT NULL,
            volume_24h    REAL NOT NULL DEFAULT 0,
            close_time    TEXT,
            UNIQUE(scan_ts, ticker)
        );
        CREATE TABLE IF NOT EXISTS executions (
            id             INTEGER PRIMARY KEY,
            execution_id   TEXT NOT NULL UNIQUE,
            created_at     TEXT NOT NULL,
            match_key      TEXT NOT NULL,
            status         TEXT NOT NULL DEFAULT 'pending',
            leg_count      INTEGER NOT NULL,
            profit         REAL,
            completed_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS execution_legs (
            id             INTEGER PRIMARY KEY,
            execution_id   TEXT NOT NULL,
            leg_index      INTEGER NOT NULL,
            platform       TEXT NOT NULL,
            ticker         TEXT NOT NULL,
            side           TEXT NOT NULL,
            action         TEXT NOT NULL,
            price          REAL NOT NULL,
            size           REAL NOT NULL,
            status         TEXT NOT NULL DEFAULT 'pending',
            order_id       TEXT,
            fill_qty       REAL,
            error          TEXT,
            sent_at        TEXT,
            completed_at   TEXT,
            UNIQUE(execution_id, leg_index)
        );
    """)
    # Insert some data to verify it's preserved
    conn.execute(
        "INSERT INTO polymarket_snapshots "
        "(scan_ts, condition_id, question, yes_bid, yes_ask, no_bid, no_ask, volume, volume_24h) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("2026-04-06T12:00:00Z", "0xabc", "Test?", 0.4, 0.5, 0.5, 0.6, 10000, 100),
    )
    conn.commit()
    conn.close()

    # Now run alembic upgrade — should not error
    cfg = _alembic_config(db_path)
    command.upgrade(cfg, "head")

    # Verify data is preserved and new table exists
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as c:
        count = c.execute(text("SELECT COUNT(*) FROM polymarket_snapshots")).scalar()
        assert count == 1
    inspector = inspect(engine)
    assert "match_snapshots" in inspector.get_table_names()
    engine.dispose()
