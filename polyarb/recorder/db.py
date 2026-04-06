"""SQLite storage for market snapshots."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from polyarb.models import Market

SCHEMA = """
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

CREATE INDEX IF NOT EXISTS idx_poly_condition ON polymarket_snapshots(condition_id, scan_ts);
CREATE INDEX IF NOT EXISTS idx_kalshi_ticker  ON kalshi_snapshots(ticker, scan_ts);
CREATE INDEX IF NOT EXISTS idx_poly_scan  ON polymarket_snapshots(scan_ts);
CREATE INDEX IF NOT EXISTS idx_kalshi_scan ON kalshi_snapshots(scan_ts);
"""

# Polymarket volume is in dollars; Kalshi volume_fp is in contracts (~$0.01–$0.99 each).
# At typical prices the thresholds are roughly comparable.  Separate per-platform
# thresholds can be added once recorded data reveals the actual distributions.
MIN_VOLUME = 10_000

_POLY_COLUMNS = (
    "scan_ts", "condition_id", "question", "event_slug",
    "yes_bid", "yes_ask", "no_bid", "no_ask",
    "volume", "volume_24h", "end_date",
)

_KALSHI_COLUMNS = (
    "scan_ts", "ticker", "question", "event_ticker",
    "yes_bid", "yes_ask", "no_bid", "no_ask",
    "volume", "volume_24h", "close_time",
)


class RecorderDB:
    def __init__(self, path: str | Path = "snapshots.db") -> None:
        self._path = str(path)
        self._conn = sqlite3.connect(self._path)
        self._conn.executescript(SCHEMA)

    @staticmethod
    def _passes_filter(m: Market) -> bool:
        return m.volume >= MIN_VOLUME and m.volume_24h > 0

    def _insert(self, table: str, columns: tuple[str, ...], rows: list[tuple]) -> int:
        """Insert rows, returning the number actually written (ignoring dupes)."""
        if not rows:
            return 0
        placeholders = ", ".join("?" for _ in columns)
        col_names = ", ".join(columns)
        before = self._conn.total_changes
        self._conn.executemany(
            f"INSERT OR IGNORE INTO {table} ({col_names}) VALUES ({placeholders})",
            rows,
        )
        self._conn.commit()
        return self._conn.total_changes - before

    @staticmethod
    def _market_row(scan_ts: str, m: Market) -> tuple:
        return (
            scan_ts,
            m.condition_id,
            m.question,
            m.event_slug,
            m.yes_token.best_bid,
            m.yes_token.best_ask,
            m.no_token.best_bid,
            m.no_token.best_ask,
            m.volume,
            m.volume_24h,
            m.end_date.isoformat() if m.end_date else None,
        )

    def insert_polymarket(self, scan_ts: str, markets: list[Market]) -> int:
        rows = [self._market_row(scan_ts, m) for m in markets if self._passes_filter(m)]
        return self._insert("polymarket_snapshots", _POLY_COLUMNS, rows)

    def insert_kalshi(self, scan_ts: str, markets: list[Market]) -> int:
        rows = [self._market_row(scan_ts, m) for m in markets if self._passes_filter(m)]
        return self._insert("kalshi_snapshots", _KALSHI_COLUMNS, rows)

    def scan_count(self) -> dict[str, int]:
        poly = self._conn.execute(
            "SELECT COUNT(DISTINCT scan_ts) FROM polymarket_snapshots"
        ).fetchone()[0]
        kalshi = self._conn.execute(
            "SELECT COUNT(DISTINCT scan_ts) FROM kalshi_snapshots"
        ).fetchone()[0]
        return {"polymarket": poly, "kalshi": kalshi}

    def market_count(self) -> dict[str, int]:
        poly = self._conn.execute(
            "SELECT COUNT(*) FROM polymarket_snapshots"
        ).fetchone()[0]
        kalshi = self._conn.execute(
            "SELECT COUNT(*) FROM kalshi_snapshots"
        ).fetchone()[0]
        return {"polymarket": poly, "kalshi": kalshi}

    def close(self) -> None:
        self._conn.close()
