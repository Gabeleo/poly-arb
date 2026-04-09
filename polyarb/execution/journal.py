"""SQLite-backed execution journal for durable order tracking.

Thin wrapper around SqliteExecutionRepository — preserves the original
public interface so CrossExecutor and existing tests need no changes.
"""

from __future__ import annotations

import sqlite3

from polyarb.observability import metrics


class ExecutionJournal:
    """Durable log of every execution attempt and its legs."""

    def __init__(self, db_path: str = "polyarb.db") -> None:
        from polyarb.db.engine import create_engine
        from polyarb.db.models import metadata
        from polyarb.db.repositories.executions import SqliteExecutionRepository

        self._db_path = db_path
        url = f"sqlite:///{db_path}"
        self._engine = create_engine(url)
        metadata.create_all(self._engine)
        self._repo = SqliteExecutionRepository(self._engine)

        # Backward-compat: tests verify rows via _conn
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def record_execution(
        self,
        execution_id: str,
        match_key: str,
        leg_count: int,
        idempotency_key: str | None = None,
    ) -> None:
        self._repo.record_execution(execution_id, match_key, leg_count, idempotency_key)

    def find_by_idempotency_key(self, key: str) -> dict | None:
        return self._repo.find_by_idempotency_key(key)

    def record_completion(
        self,
        execution_id: str,
        success: bool,
        profit: float | None = None,
    ) -> None:
        self._repo.record_completion(execution_id, success, profit)

    def record_attempt(
        self,
        execution_id: str,
        leg_index: int,
        platform: str,
        ticker: str,
        side: str,
        action: str,
        price: float,
        size: float,
    ) -> int:
        return self._repo.record_attempt(
            execution_id,
            leg_index,
            platform,
            ticker,
            side,
            action,
            price,
            size,
        )

    def mark_sent(self, row_id: int) -> None:
        self._repo.mark_sent(row_id)
        metrics.orphaned_legs.set(len(self._repo.get_orphans()))

    def record_result(
        self,
        row_id: int,
        order_id: str | None,
        status: str,
        fill_qty: float | None = None,
        error: str | None = None,
    ) -> None:
        self._repo.record_result(row_id, order_id, status, fill_qty, error)
        metrics.orphaned_legs.set(len(self._repo.get_orphans()))

    def record_cancel(self, row_id: int, cancel_status: str) -> None:
        self._repo.record_cancel(row_id, cancel_status)

    def get_orphans(self) -> list[dict]:
        orphans = self._repo.get_orphans()
        metrics.orphaned_legs.set(len(orphans))
        return orphans

    def count_by_status(self, status: str) -> int:
        return self._repo.count_by_status(status)

    def get_history(self, limit: int = 50) -> list[dict]:
        return self._repo.get_history(limit)

    def mark_orphaned(self, row_id: int) -> None:
        self._repo.mark_orphaned(row_id)

    def close(self) -> None:
        self._conn.close()
        self._engine.dispose()
