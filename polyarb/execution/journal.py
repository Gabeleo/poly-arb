"""SQLite-backed execution journal for durable order tracking.

Records every leg placement attempt so that on restart, orphaned
positions can be detected and resolved.  Uses WAL mode for concurrent
read/write safety.

No external dependencies — sqlite3 is stdlib.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionJournal:
    """Durable log of every execution attempt and its legs."""

    def __init__(self, db_path: str = "executions.db") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
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

            CREATE INDEX IF NOT EXISTS idx_legs_execution
                ON execution_legs(execution_id);
            CREATE INDEX IF NOT EXISTS idx_legs_status
                ON execution_legs(status);
            CREATE INDEX IF NOT EXISTS idx_exec_status
                ON executions(status);
        """)

    # ── Execution-level operations ────────────────────────

    def record_execution(
        self, execution_id: str, match_key: str, leg_count: int,
    ) -> None:
        """Create the parent execution row (status='pending')."""
        self._conn.execute(
            "INSERT INTO executions (execution_id, created_at, match_key, leg_count) "
            "VALUES (?, ?, ?, ?)",
            (execution_id, _now(), match_key, leg_count),
        )
        self._conn.commit()

    def record_completion(
        self, execution_id: str, success: bool, profit: float | None = None,
    ) -> None:
        """Mark an execution as completed or failed."""
        status = "completed" if success else "failed"
        self._conn.execute(
            "UPDATE executions SET status=?, profit=?, completed_at=? "
            "WHERE execution_id=?",
            (status, profit, _now(), execution_id),
        )
        self._conn.commit()

    # ── Leg-level operations ──────────────────────────────

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
        """Record a pending leg before the API call. Returns the row id."""
        cur = self._conn.execute(
            "INSERT INTO execution_legs "
            "(execution_id, leg_index, platform, ticker, side, action, price, size) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (execution_id, leg_index, platform, ticker, side, action, price, size),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def mark_sent(self, row_id: int) -> None:
        """Mark a leg as sent (API call in flight)."""
        self._conn.execute(
            "UPDATE execution_legs SET status='sent', sent_at=? WHERE id=?",
            (_now(), row_id),
        )
        self._conn.commit()

    def record_result(
        self,
        row_id: int,
        order_id: str | None,
        status: str,
        fill_qty: float | None = None,
        error: str | None = None,
    ) -> None:
        """Record the API response for a leg."""
        self._conn.execute(
            "UPDATE execution_legs "
            "SET order_id=?, status=?, fill_qty=?, error=?, completed_at=? "
            "WHERE id=?",
            (order_id, status, fill_qty, error, _now(), row_id),
        )
        self._conn.commit()

    def record_cancel(self, row_id: int, cancel_status: str) -> None:
        """Record a cancellation attempt for a leg."""
        self._conn.execute(
            "UPDATE execution_legs SET status=?, completed_at=? WHERE id=?",
            (cancel_status, _now(), row_id),
        )
        self._conn.commit()

    # ── Queries ───────────────────────────────────────────

    def get_orphans(self) -> list[dict]:
        """Return legs with status='sent' and no completed result."""
        rows = self._conn.execute(
            "SELECT * FROM execution_legs WHERE status='sent'"
        ).fetchall()
        return [dict(r) for r in rows]

    def count_by_status(self, status: str) -> int:
        """Count legs with the given status."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM execution_legs WHERE status=?", (status,)
        ).fetchone()
        return row[0]

    def get_history(self, limit: int = 50) -> list[dict]:
        """Return recent executions with their legs."""
        rows = self._conn.execute(
            "SELECT * FROM executions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

        result = []
        for row in rows:
            exec_dict = dict(row)
            legs = self._conn.execute(
                "SELECT * FROM execution_legs WHERE execution_id=? ORDER BY leg_index",
                (exec_dict["execution_id"],),
            ).fetchall()
            exec_dict["legs"] = [dict(leg) for leg in legs]
            result.append(exec_dict)
        return result

    def mark_orphaned(self, row_id: int) -> None:
        """Mark a sent leg as orphaned (reviewed but unresolved)."""
        self._conn.execute(
            "UPDATE execution_legs SET status='orphaned' WHERE id=?",
            (row_id,),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
