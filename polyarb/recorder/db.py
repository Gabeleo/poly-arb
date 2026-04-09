"""SQLite storage for market snapshots.

Thin wrapper around SqliteSnapshotRepository — preserves the original
public interface so recorder.py and existing tests need no changes.
"""

from __future__ import annotations

from pathlib import Path

from polyarb.models import Market

# Kept for backward compat (test_backtest.py imports this)
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

# Volume filter — business logic, not data access.
MIN_VOLUME = 10_000


class RecorderDB:
    def __init__(self, path: str | Path = "polyarb.db") -> None:
        from polyarb.db.engine import create_engine
        from polyarb.db.models import metadata
        from polyarb.db.repositories.snapshots import SqliteSnapshotRepository

        url = f"sqlite:///{path}"
        self._engine = create_engine(url)
        metadata.create_all(self._engine)
        self._repo = SqliteSnapshotRepository(self._engine)

    @staticmethod
    def _passes_filter(m: Market) -> bool:
        return m.volume >= MIN_VOLUME and m.volume_24h > 0

    @staticmethod
    def _poly_dict(scan_ts: str, m: Market) -> dict:
        return {
            "scan_ts": scan_ts,
            "condition_id": m.condition_id,
            "question": m.question,
            "event_slug": m.event_slug,
            "yes_bid": m.yes_token.best_bid,
            "yes_ask": m.yes_token.best_ask,
            "no_bid": m.no_token.best_bid,
            "no_ask": m.no_token.best_ask,
            "volume": m.volume,
            "volume_24h": m.volume_24h,
            "end_date": m.end_date.isoformat() if m.end_date else None,
        }

    @staticmethod
    def _kalshi_dict(scan_ts: str, m: Market) -> dict:
        return {
            "scan_ts": scan_ts,
            "ticker": m.condition_id,
            "question": m.question,
            "event_ticker": m.event_slug,
            "yes_bid": m.yes_token.best_bid,
            "yes_ask": m.yes_token.best_ask,
            "no_bid": m.no_token.best_bid,
            "no_ask": m.no_token.best_ask,
            "volume": m.volume,
            "volume_24h": m.volume_24h,
            "close_time": m.end_date.isoformat() if m.end_date else None,
        }

    def insert_polymarket(self, scan_ts: str, markets: list[Market]) -> int:
        rows = [self._poly_dict(scan_ts, m) for m in markets if self._passes_filter(m)]
        return self._repo.insert_polymarket(scan_ts, rows)

    def insert_kalshi(self, scan_ts: str, markets: list[Market]) -> int:
        rows = [self._kalshi_dict(scan_ts, m) for m in markets if self._passes_filter(m)]
        return self._repo.insert_kalshi(scan_ts, rows)

    def scan_count(self) -> dict[str, int]:
        return self._repo.scan_count()

    def market_count(self) -> dict[str, int]:
        return self._repo.market_count()

    def close(self) -> None:
        self._engine.dispose()
