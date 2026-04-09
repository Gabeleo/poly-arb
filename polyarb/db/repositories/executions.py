"""Execution repository — read/write execution journal via SQLAlchemy Core."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine

from polyarb.db.models import execution_legs, executions


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ExecutionRepository(Protocol):
    """Read/write execution journal."""

    def record_execution(
        self, execution_id: str, match_key: str, leg_count: int, idempotency_key: str | None = None
    ) -> None: ...
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
    ) -> int: ...
    def mark_sent(self, row_id: int) -> None: ...
    def record_result(
        self,
        row_id: int,
        order_id: str | None,
        status: str,
        fill_qty: float | None = None,
        error: str | None = None,
    ) -> None: ...
    def record_cancel(self, row_id: int, cancel_status: str) -> None: ...
    def record_completion(
        self, execution_id: str, success: bool, profit: float | None = None
    ) -> None: ...
    def find_by_idempotency_key(self, key: str) -> dict | None: ...
    def get_orphans(self) -> list[dict]: ...
    def mark_orphaned(self, row_id: int) -> None: ...
    def count_by_status(self, status: str) -> int: ...
    def get_history(self, limit: int = 50) -> list[dict]: ...


class SqliteExecutionRepository:
    """Sync SQLite-backed execution repository."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record_execution(
        self,
        execution_id: str,
        match_key: str,
        leg_count: int,
        idempotency_key: str | None = None,
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(executions).values(
                    execution_id=execution_id,
                    created_at=_now(),
                    match_key=match_key,
                    leg_count=leg_count,
                    idempotency_key=idempotency_key,
                )
            )

    def find_by_idempotency_key(self, key: str) -> dict | None:
        """Return the most recent non-failed execution with this key, or None."""
        with self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(executions)
                    .where(executions.c.idempotency_key == key)
                    .where(executions.c.status != "failed")
                    .order_by(executions.c.id.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

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
        with self._engine.begin() as conn:
            result = conn.execute(
                insert(execution_legs).values(
                    execution_id=execution_id,
                    leg_index=leg_index,
                    platform=platform,
                    ticker=ticker,
                    side=side,
                    action=action,
                    price=price,
                    size=size,
                )
            )
            pk = result.inserted_primary_key
            if pk is None:
                raise RuntimeError("INSERT did not return a primary key")
            return pk[0]

    def mark_sent(self, row_id: int) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(execution_legs)
                .where(execution_legs.c.id == row_id)
                .values(status="sent", sent_at=_now())
            )

    def record_result(
        self,
        row_id: int,
        order_id: str | None,
        status: str,
        fill_qty: float | None = None,
        error: str | None = None,
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(execution_legs)
                .where(execution_legs.c.id == row_id)
                .values(
                    order_id=order_id,
                    status=status,
                    fill_qty=fill_qty,
                    error=error,
                    completed_at=_now(),
                )
            )

    def record_cancel(self, row_id: int, cancel_status: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(execution_legs)
                .where(execution_legs.c.id == row_id)
                .values(status=cancel_status, completed_at=_now())
            )

    def record_completion(
        self,
        execution_id: str,
        success: bool,
        profit: float | None = None,
    ) -> None:
        status = "completed" if success else "failed"
        with self._engine.begin() as conn:
            conn.execute(
                update(executions)
                .where(executions.c.execution_id == execution_id)
                .values(status=status, profit=profit, completed_at=_now())
            )

    def get_orphans(self) -> list[dict]:
        with self._engine.connect() as conn:
            rows = (
                conn.execute(select(execution_legs).where(execution_legs.c.status == "sent"))
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    def mark_orphaned(self, row_id: int) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(execution_legs)
                .where(execution_legs.c.id == row_id)
                .values(status="orphaned")
            )

    def count_by_status(self, status: str) -> int:
        with self._engine.connect() as conn:
            return conn.execute(
                select(func.count())
                .select_from(execution_legs)
                .where(execution_legs.c.status == status)
            ).scalar_one()

    def get_history(self, limit: int = 50) -> list[dict]:
        with self._engine.connect() as conn:
            rows = (
                conn.execute(select(executions).order_by(executions.c.id.desc()).limit(limit))
                .mappings()
                .all()
            )

            result = []
            for row in rows:
                exec_dict = dict(row)
                legs = (
                    conn.execute(
                        select(execution_legs)
                        .where(execution_legs.c.execution_id == exec_dict["execution_id"])
                        .order_by(execution_legs.c.leg_index)
                    )
                    .mappings()
                    .all()
                )
                exec_dict["legs"] = [dict(leg) for leg in legs]
                result.append(exec_dict)
        return result
