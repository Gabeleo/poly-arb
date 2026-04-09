"""Performance attribution — which pairs and directions drove P&L.

Joins execution data with match snapshots to attribute profit to
specific matched pairs and arb directions.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from polyarb.db.models import execution_legs, executions


@dataclass(frozen=True)
class PairAttribution:
    """Profit attribution for one matched pair."""

    match_key: str
    trade_count: int
    total_profit: float
    avg_profit: float
    win_count: int
    loss_count: int

    @property
    def win_rate(self) -> float:
        if self.trade_count == 0:
            return 0.0
        return self.win_count / self.trade_count

    def to_dict(self) -> dict:
        return {
            "match_key": self.match_key,
            "trade_count": self.trade_count,
            "total_profit": round(self.total_profit, 4),
            "avg_profit": round(self.avg_profit, 4),
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate": round(self.win_rate, 4),
        }


@dataclass(frozen=True)
class PlatformAttribution:
    """Profit attribution for one platform leg direction."""

    platform: str
    side: str
    trade_count: int
    total_profit: float

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "side": self.side,
            "trade_count": self.trade_count,
            "total_profit": round(self.total_profit, 4),
        }


@dataclass(frozen=True)
class PerformanceSummary:
    """Full performance attribution."""

    total_trades: int
    total_profit: float
    win_count: int
    loss_count: int
    by_pair: list[PairAttribution]
    by_platform: list[PlatformAttribution]

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.win_count / self.total_trades

    def to_dict(self) -> dict:
        return {
            "total_trades": self.total_trades,
            "total_profit": round(self.total_profit, 4),
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate": round(self.win_rate, 4),
            "by_pair": [p.to_dict() for p in self.by_pair],
            "by_platform": [p.to_dict() for p in self.by_platform],
        }


class SqlitePerformanceProvider:
    """Computes performance attribution from execution data."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def summary(self) -> PerformanceSummary:
        by_pair = self._by_pair()
        by_platform = self._by_platform()

        total_trades = sum(p.trade_count for p in by_pair)
        total_profit = sum(p.total_profit for p in by_pair)
        win_count = sum(p.win_count for p in by_pair)
        loss_count = sum(p.loss_count for p in by_pair)

        return PerformanceSummary(
            total_trades=total_trades,
            total_profit=total_profit,
            win_count=win_count,
            loss_count=loss_count,
            by_pair=by_pair,
            by_platform=by_platform,
        )

    def _by_pair(self) -> list[PairAttribution]:
        stmt = (
            select(
                executions.c.match_key,
                func.count().label("trade_count"),
                func.coalesce(func.sum(executions.c.profit), 0.0).label("total_profit"),
                func.coalesce(func.avg(executions.c.profit), 0.0).label("avg_profit"),
                func.sum(func.iif(executions.c.profit > 0, 1, 0)).label("win_count"),
                func.sum(func.iif(executions.c.profit <= 0, 1, 0)).label("loss_count"),
            )
            .where(executions.c.status == "completed")
            .where(executions.c.profit.isnot(None))
            .group_by(executions.c.match_key)
            .order_by(func.sum(executions.c.profit).desc())
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()

        return [
            PairAttribution(
                match_key=r["match_key"],
                trade_count=int(r["trade_count"]),
                total_profit=float(r["total_profit"]),
                avg_profit=float(r["avg_profit"]),
                win_count=int(r["win_count"]),
                loss_count=int(r["loss_count"]),
            )
            for r in rows
        ]

    def _by_platform(self) -> list[PlatformAttribution]:
        """Attribute profit to platform + side from execution legs.

        Each completed execution has a profit on the parent row.
        We split it evenly across legs (each leg contributed to the arb).
        """
        # Join legs to their parent execution, get platform/side with split profit
        leg_profit = (executions.c.profit / executions.c.leg_count).label("leg_profit")
        stmt = (
            select(
                execution_legs.c.platform,
                execution_legs.c.side,
                func.count().label("trade_count"),
                func.coalesce(func.sum(leg_profit), 0.0).label("total_profit"),
            )
            .select_from(
                execution_legs.join(
                    executions,
                    execution_legs.c.execution_id == executions.c.execution_id,
                )
            )
            .where(executions.c.status == "completed")
            .where(executions.c.profit.isnot(None))
            .where(execution_legs.c.status == "filled")
            .group_by(execution_legs.c.platform, execution_legs.c.side)
            .order_by(func.sum(leg_profit).desc())
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()

        return [
            PlatformAttribution(
                platform=r["platform"],
                side=r["side"],
                trade_count=int(r["trade_count"]),
                total_profit=float(r["total_profit"]),
            )
            for r in rows
        ]
