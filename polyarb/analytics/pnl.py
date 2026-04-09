"""P&L tracking — realized + unrealized, total/daily/per-pair.

Unrealized P&L is computed at query time by joining open positions
against the most recent snapshot prices.  Positions do not carry
current_price — it is always resolved from snapshot data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from polyarb.db.models import (
    executions,
    kalshi_snapshots,
    polymarket_snapshots,
    positions,
)


class PnLProvider(Protocol):
    """Read-only interface for P&L queries."""

    def summary(self) -> PnLSummary: ...
    def daily(self, lookback_days: int = 30) -> list[DailyPnL]: ...
    def per_pair(self) -> list[PairPnL]: ...


@dataclass(frozen=True)
class PnLSummary:
    """Aggregate P&L across all positions."""

    total_realized: float
    total_unrealized: float
    open_positions: int
    closed_positions: int

    @property
    def total(self) -> float:
        return self.total_realized + self.total_unrealized

    def to_dict(self) -> dict:
        return {
            "total_realized": round(self.total_realized, 4),
            "total_unrealized": round(self.total_unrealized, 4),
            "total": round(self.total, 4),
            "open_positions": self.open_positions,
            "closed_positions": self.closed_positions,
        }


@dataclass(frozen=True)
class DailyPnL:
    """P&L for a single day."""

    date: str
    realized: float
    trade_count: int

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "realized": round(self.realized, 4),
            "trade_count": self.trade_count,
        }


@dataclass(frozen=True)
class PairPnL:
    """P&L breakdown for one position."""

    platform: str
    ticker: str
    side: str
    quantity: float
    avg_price: float
    current_price: float | None
    unrealized: float
    realized: float | None
    is_open: bool

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "ticker": self.ticker,
            "side": self.side,
            "quantity": self.quantity,
            "avg_price": round(self.avg_price, 4),
            "current_price": round(self.current_price, 4)
            if self.current_price is not None
            else None,
            "unrealized": round(self.unrealized, 4),
            "realized": round(self.realized, 4) if self.realized is not None else None,
            "is_open": self.is_open,
        }


class SqlitePnLProvider:
    """Computes P&L from positions + snapshot data via SQLAlchemy."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def summary(self) -> PnLSummary:
        with self._engine.connect() as conn:
            # Realized: sum of realized_pnl on closed positions
            realized = conn.execute(
                select(func.coalesce(func.sum(positions.c.realized_pnl), 0.0)).where(
                    positions.c.closed_at.isnot(None)
                )
            ).scalar()

            # Count open/closed
            open_count = conn.execute(
                select(func.count()).select_from(positions).where(positions.c.closed_at.is_(None))
            ).scalar()
            closed_count = conn.execute(
                select(func.count()).select_from(positions).where(positions.c.closed_at.isnot(None))
            ).scalar()

        # Unrealized: sum across open positions
        pairs = self.per_pair()
        unrealized = sum(p.unrealized for p in pairs if p.is_open)

        return PnLSummary(
            total_realized=float(realized or 0),
            total_unrealized=unrealized,
            open_positions=int(open_count or 0),
            closed_positions=int(closed_count or 0),
        )

    def daily(self, lookback_days: int = 30) -> list[DailyPnL]:
        cutoff = (datetime.now(UTC) - timedelta(days=lookback_days)).isoformat()

        # Daily realized P&L from completed executions
        date_expr = func.substr(executions.c.completed_at, 1, 10)
        stmt = (
            select(
                date_expr.label("date"),
                func.coalesce(func.sum(executions.c.profit), 0.0).label("realized"),
                func.count().label("trade_count"),
            )
            .where(executions.c.status == "completed")
            .where(executions.c.completed_at >= cutoff)
            .group_by(date_expr)
            .order_by(date_expr)
        )

        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()

        return [
            DailyPnL(
                date=r["date"],
                realized=float(r["realized"]),
                trade_count=int(r["trade_count"]),
            )
            for r in rows
        ]

    def per_pair(self) -> list[PairPnL]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(positions).order_by(positions.c.id)).mappings().all()

        results = []
        for row in rows:
            is_open = row["closed_at"] is None
            current_price = None
            unrealized = 0.0

            if is_open:
                current_price = self._latest_price(row["platform"], row["ticker"], row["side"])
                if current_price is not None:
                    unrealized = (current_price - row["avg_price"]) * row["quantity"]

            results.append(
                PairPnL(
                    platform=row["platform"],
                    ticker=row["ticker"],
                    side=row["side"],
                    quantity=row["quantity"],
                    avg_price=row["avg_price"],
                    current_price=current_price,
                    unrealized=unrealized,
                    realized=row["realized_pnl"],
                    is_open=is_open,
                )
            )
        return results

    def _latest_price(self, platform: str, ticker: str, side: str) -> float | None:
        """Get the most recent snapshot price for a market/side."""
        if platform == "polymarket":
            col = polymarket_snapshots.c.yes_bid if side == "yes" else polymarket_snapshots.c.no_bid
            stmt = (
                select(col)
                .where(polymarket_snapshots.c.condition_id == ticker)
                .order_by(polymarket_snapshots.c.scan_ts.desc())
                .limit(1)
            )
        elif platform == "kalshi":
            col = kalshi_snapshots.c.yes_bid if side == "yes" else kalshi_snapshots.c.no_bid
            stmt = (
                select(col)
                .where(kalshi_snapshots.c.ticker == ticker)
                .order_by(kalshi_snapshots.c.scan_ts.desc())
                .limit(1)
            )
        else:
            return None

        with self._engine.connect() as conn:
            result = conn.execute(stmt).scalar()
        return float(result) if result is not None else None
