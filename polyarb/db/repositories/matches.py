"""Match snapshot repository — read/write match data per scan cycle."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from polyarb.db.models import match_snapshots


class MatchSnapshotRepository(Protocol):
    """Read/write match snapshot data."""

    def insert_matches(self, scan_ts: str, scan_id: str, matches: list[dict]) -> int: ...
    def get_pair_history(self, poly_cid: str, kalshi_ticker: str) -> list[dict]: ...
    def get_recorded_pairs(self) -> list[tuple[str, str]]: ...
    def get_scan_matches(self, scan_ts: str) -> list[dict]: ...


class SqliteMatchSnapshotRepository:
    """Sync SQLite-backed match snapshot repository."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def insert_matches(self, scan_ts: str, scan_id: str, matches: list[dict]) -> int:
        """Insert match snapshot rows from a scan cycle.

        Each match dict must contain: poly_condition_id, kalshi_ticker,
        poly_question, kalshi_question, confidence, and 8 price fields.
        raw_delta is computed as abs(poly_yes_ask - (1 - kalshi_no_ask)).
        """
        if not matches:
            return 0
        rows = []
        for m in matches:
            poly_yes_ask = m["poly_yes_ask"]
            kalshi_no_ask = 1.0 - m["kalshi_no_bid"]  # best ask for NO side
            raw_delta = abs(poly_yes_ask - kalshi_no_ask) if kalshi_no_ask else 0.0
            rows.append(
                {
                    "scan_ts": scan_ts,
                    "scan_id": scan_id,
                    "poly_condition_id": m["poly_condition_id"],
                    "kalshi_ticker": m["kalshi_ticker"],
                    "poly_question": m["poly_question"],
                    "kalshi_question": m["kalshi_question"],
                    "confidence": m["confidence"],
                    "poly_yes_bid": m["poly_yes_bid"],
                    "poly_yes_ask": m["poly_yes_ask"],
                    "poly_no_bid": m["poly_no_bid"],
                    "poly_no_ask": m["poly_no_ask"],
                    "kalshi_yes_bid": m["kalshi_yes_bid"],
                    "kalshi_yes_ask": m["kalshi_yes_ask"],
                    "kalshi_no_bid": m["kalshi_no_bid"],
                    "kalshi_no_ask": m["kalshi_no_ask"],
                    "raw_delta": round(raw_delta, 6),
                }
            )
        with self._engine.begin() as conn:
            conn.execute(insert(match_snapshots).prefix_with("OR IGNORE"), rows)
        return len(rows)

    def get_pair_history(self, poly_cid: str, kalshi_ticker: str) -> list[dict]:
        """Return all recorded snapshots for a matched pair, ordered by scan_ts."""
        stmt = (
            select(match_snapshots)
            .where(match_snapshots.c.poly_condition_id == poly_cid)
            .where(match_snapshots.c.kalshi_ticker == kalshi_ticker)
            .order_by(match_snapshots.c.scan_ts)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [dict(r) for r in rows]

    def get_recorded_pairs(self) -> list[tuple[str, str]]:
        """Return all unique (poly_cid, kalshi_ticker) pairs in the table."""
        stmt = select(
            match_snapshots.c.poly_condition_id,
            match_snapshots.c.kalshi_ticker,
        ).distinct()
        with self._engine.connect() as conn:
            return [(r[0], r[1]) for r in conn.execute(stmt).fetchall()]

    def get_scan_matches(self, scan_ts: str) -> list[dict]:
        """Return all matches for a specific scan timestamp."""
        stmt = select(match_snapshots).where(match_snapshots.c.scan_ts == scan_ts)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [dict(r) for r in rows]
