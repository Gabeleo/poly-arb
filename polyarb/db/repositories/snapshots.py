"""Snapshot repository — read/write market snapshots via SQLAlchemy Core."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import insert, select, func, text
from sqlalchemy.engine import Engine

from polyarb.db.models import kalshi_snapshots, polymarket_snapshots


class SnapshotRepository(Protocol):
    """Read/write market snapshots."""

    def insert_polymarket(self, scan_ts: str, rows: list[dict]) -> int: ...
    def insert_kalshi(self, scan_ts: str, rows: list[dict]) -> int: ...
    def scan_count(self) -> dict[str, int]: ...
    def market_count(self) -> dict[str, int]: ...
    def get_pair_scans(self, poly_cid: str, kalshi_ticker: str) -> list[dict]: ...
    def get_distinct_scan_timestamps(self) -> list[str]: ...
    def get_pair_scan_at(self, poly_cid: str, kalshi_ticker: str, scan_ts: str) -> dict | None: ...


class SqliteSnapshotRepository:
    """Sync SQLite-backed snapshot repository."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def insert_polymarket(self, scan_ts: str, rows: list[dict]) -> int:
        """Batch insert Polymarket snapshots. Returns count inserted."""
        if not rows:
            return 0
        with self._engine.begin() as conn:
            before = conn.execute(
                select(func.count()).select_from(polymarket_snapshots)
            ).scalar_one()
            stmt = insert(polymarket_snapshots).prefix_with("OR IGNORE")
            conn.execute(stmt, rows)
            after = conn.execute(
                select(func.count()).select_from(polymarket_snapshots)
            ).scalar_one()
        return after - before

    def insert_kalshi(self, scan_ts: str, rows: list[dict]) -> int:
        """Batch insert Kalshi snapshots. Returns count inserted."""
        if not rows:
            return 0
        with self._engine.begin() as conn:
            before = conn.execute(
                select(func.count()).select_from(kalshi_snapshots)
            ).scalar_one()
            stmt = insert(kalshi_snapshots).prefix_with("OR IGNORE")
            conn.execute(stmt, rows)
            after = conn.execute(
                select(func.count()).select_from(kalshi_snapshots)
            ).scalar_one()
        return after - before

    def scan_count(self) -> dict[str, int]:
        with self._engine.connect() as conn:
            poly = conn.execute(
                select(func.count(polymarket_snapshots.c.scan_ts.distinct()))
            ).scalar_one()
            kalshi = conn.execute(
                select(func.count(kalshi_snapshots.c.scan_ts.distinct()))
            ).scalar_one()
        return {"polymarket": poly, "kalshi": kalshi}

    def market_count(self) -> dict[str, int]:
        with self._engine.connect() as conn:
            poly = conn.execute(
                select(func.count()).select_from(polymarket_snapshots)
            ).scalar_one()
            kalshi = conn.execute(
                select(func.count()).select_from(kalshi_snapshots)
            ).scalar_one()
        return {"polymarket": poly, "kalshi": kalshi}

    def get_pair_scans(self, poly_cid: str, kalshi_ticker: str) -> list[dict]:
        """Return all scans for a matched pair (for lifetime analysis)."""
        p = polymarket_snapshots
        k = kalshi_snapshots
        stmt = (
            select(
                p.c.scan_ts,
                p.c.yes_ask.label("poly_yes_ask"),
                p.c.no_ask.label("poly_no_ask"),
                k.c.yes_ask.label("kalshi_yes_ask"),
                k.c.no_ask.label("kalshi_no_ask"),
                p.c.question.label("poly_question"),
                k.c.question.label("kalshi_question"),
            )
            .select_from(p.join(k, p.c.scan_ts == k.c.scan_ts))
            .where(p.c.condition_id == poly_cid)
            .where(k.c.ticker == kalshi_ticker)
            .order_by(p.c.scan_ts)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [dict(r) for r in rows]

    def get_distinct_scan_timestamps(self) -> list[str]:
        """Return all distinct scan timestamps, ordered."""
        stmt = (
            select(polymarket_snapshots.c.scan_ts.distinct())
            .order_by(polymarket_snapshots.c.scan_ts)
        )
        with self._engine.connect() as conn:
            return [r[0] for r in conn.execute(stmt).fetchall()]

    def get_pair_scan_at(
        self, poly_cid: str, kalshi_ticker: str, scan_ts: str,
    ) -> dict | None:
        """Return a single scan row for a pair at a specific timestamp."""
        p = polymarket_snapshots
        k = kalshi_snapshots
        stmt = (
            select(
                p.c.yes_ask.label("poly_yes_ask"),
                p.c.no_ask.label("poly_no_ask"),
                k.c.yes_ask.label("kalshi_yes_ask"),
                k.c.no_ask.label("kalshi_no_ask"),
                p.c.end_date,
                k.c.close_time,
            )
            .select_from(p.join(k, p.c.scan_ts == k.c.scan_ts))
            .where(p.c.condition_id == poly_cid)
            .where(k.c.ticker == kalshi_ticker)
            .where(p.c.scan_ts == scan_ts)
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().first()
        return dict(row) if row is not None else None
