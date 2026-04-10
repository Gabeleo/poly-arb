"""Positions repository — read/write position state via SQLAlchemy Core."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine

from polyarb.db.models import positions


def _now() -> str:
    return datetime.now(UTC).isoformat()


class PositionRepository(Protocol):
    """Read/write position state."""

    def open_position(
        self,
        platform: str,
        ticker: str,
        side: str,
        quantity: float,
        avg_price: float,
        execution_id: str | None = None,
    ) -> int: ...

    def close_position(
        self,
        position_id: int,
        realized_pnl: float | None = None,
    ) -> None: ...

    def get_open_positions(self) -> list[dict]: ...

    def get_position_by_market(self, platform: str, ticker: str, side: str) -> dict | None: ...

    def update_position(
        self,
        position_id: int,
        quantity: float,
        avg_price: float,
        execution_id: str | None = None,
    ) -> None: ...

    def get_position_size(self, platform: str, ticker: str) -> float: ...

    def get_total_exposure(self) -> float: ...

    def get_all_positions(self) -> list[dict]: ...


class SqlitePositionRepository:
    """Sync SQLite-backed position repository."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def open_position(
        self,
        platform: str,
        ticker: str,
        side: str,
        quantity: float,
        avg_price: float,
        execution_id: str | None = None,
    ) -> int:
        """Insert a new open position, returning its row ID."""
        with self._engine.begin() as conn:
            result = conn.execute(
                insert(positions).values(
                    platform=platform,
                    ticker=ticker,
                    side=side,
                    quantity=quantity,
                    avg_price=avg_price,
                    opened_at=_now(),
                    execution_id=execution_id,
                )
            )
            pk = result.inserted_primary_key
            if pk is None:
                raise RuntimeError("INSERT did not return a primary key")
            return pk[0]

    def close_position(
        self,
        position_id: int,
        realized_pnl: float | None = None,
    ) -> None:
        """Mark a position as closed with optional realized P&L."""
        with self._engine.begin() as conn:
            conn.execute(
                update(positions)
                .where(positions.c.id == position_id)
                .values(closed_at=_now(), realized_pnl=realized_pnl)
            )

    def get_open_positions(self) -> list[dict]:
        """Return all positions where closed_at IS NULL."""
        with self._engine.connect() as conn:
            rows = (
                conn.execute(
                    select(positions)
                    .where(positions.c.closed_at.is_(None))
                    .order_by(positions.c.id)
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    def get_position_by_market(self, platform: str, ticker: str, side: str) -> dict | None:
        """Return the open position for a specific market/side, or None."""
        with self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(positions)
                    .where(positions.c.platform == platform)
                    .where(positions.c.ticker == ticker)
                    .where(positions.c.side == side)
                    .where(positions.c.closed_at.is_(None))
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    def get_position_size(self, platform: str, ticker: str) -> float:
        """Return current contract count for an open position."""
        with self._engine.connect() as conn:
            row = conn.execute(
                select(positions.c.quantity)
                .where(positions.c.platform == platform)
                .where(positions.c.ticker == ticker)
                .where(positions.c.closed_at.is_(None))
            ).scalar()
        return float(row) if row is not None else 0.0

    def get_total_exposure(self) -> float:
        """Return total capital at risk (sum of quantity * avg_price) for open positions."""
        with self._engine.connect() as conn:
            result = conn.execute(
                select(
                    func.coalesce(func.sum(positions.c.quantity * positions.c.avg_price), 0.0)
                ).where(positions.c.closed_at.is_(None))
            ).scalar()
        return float(result or 0)

    def update_position(
        self,
        position_id: int,
        quantity: float,
        avg_price: float,
        execution_id: str | None = None,
    ) -> None:
        """Update quantity and avg_price on an existing open position."""
        values: dict = {"quantity": quantity, "avg_price": avg_price}
        if execution_id is not None:
            values["execution_id"] = execution_id
        with self._engine.begin() as conn:
            conn.execute(update(positions).where(positions.c.id == position_id).values(**values))

    def get_all_positions(self) -> list[dict]:
        """Return all positions (open and closed), ordered by ID."""
        with self._engine.connect() as conn:
            rows = conn.execute(select(positions).order_by(positions.c.id)).mappings().all()
        return [dict(r) for r in rows]
