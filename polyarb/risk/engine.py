"""Pre-execution risk check pipeline.

Every execution request passes through the RiskEngine before reaching
exchange APIs.  If any check fails, the request is rejected with a
specific reason logged to the risk_events table.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from polyarb.risk.limits import (
    ExecutionRequest,
    RiskCheckResult,
    RiskLimits,
    check_concurrent_order_limit,
    check_daily_loss_limit,
    check_exposure_limit,
    check_order_size_limit,
    check_position_limit,
    check_trade_rate_limit,
)

logger = logging.getLogger(__name__)


class RiskDataProvider(Protocol):
    """Read-only interface for querying state needed by risk checks.

    Implementations can query SQLite, in-memory state, or test stubs.
    """

    def get_position_size(self, platform: str, ticker: str) -> float:
        """Return current contract count for a specific market."""
        ...

    def get_total_exposure(self) -> float:
        """Return total capital at risk across all open positions."""
        ...

    def get_daily_pnl(self) -> float:
        """Return rolling 24h realized + unrealized P&L (negative = loss)."""
        ...

    def get_concurrent_order_count(self) -> int:
        """Return number of orders currently in-flight (status=sent)."""
        ...


class RiskEventRecorder(Protocol):
    """Write interface for recording risk events."""

    def record_risk_event(
        self,
        event_type: str,
        severity: str,
        details: str,
        execution_id: str | None = None,
    ) -> None: ...


@dataclass(frozen=True)
class RiskVerdict:
    """Aggregate outcome of the full risk check pipeline."""

    approved: bool
    checks: tuple[RiskCheckResult, ...] = ()

    @property
    def failed_checks(self) -> list[RiskCheckResult]:
        return [c for c in self.checks if not c.passed]

    @property
    def rejection_reason(self) -> str:
        failed = self.failed_checks
        if not failed:
            return ""
        return "; ".join(f"[{c.limit_name}] {c.reason}" for c in failed)

    def __bool__(self) -> bool:
        return self.approved


@dataclass
class RiskEngine:
    """Pre-execution risk check pipeline.

    Chains all limit checks and records rejections.  The engine does NOT
    execute trades — it only gates them.

    Usage::

        verdict = engine.evaluate(request)
        if not verdict:
            log.warning("Rejected: %s", verdict.rejection_reason)
            return
        # proceed with execution
    """

    limits: RiskLimits
    data_provider: RiskDataProvider
    event_recorder: RiskEventRecorder | None = None
    _last_trade_ts: float | None = field(default=None, init=False, repr=False)

    def evaluate(self, request: ExecutionRequest) -> RiskVerdict:
        """Run all risk checks.  Returns a RiskVerdict."""
        checks = [
            check_order_size_limit(request, self.limits),
            check_position_limit(
                request,
                self.limits,
                current_position=self._get_max_position(request),
            ),
            check_exposure_limit(
                request,
                self.limits,
                current_exposure=self.data_provider.get_total_exposure(),
            ),
            check_daily_loss_limit(
                self.limits,
                daily_loss=self.data_provider.get_daily_pnl(),
            ),
            check_concurrent_order_limit(
                self.limits,
                concurrent_orders=self.data_provider.get_concurrent_order_count(),
            ),
            check_trade_rate_limit(
                self.limits,
                last_trade_ts=self._last_trade_ts,
            ),
        ]

        approved = all(checks)
        verdict = RiskVerdict(approved=approved, checks=tuple(checks))

        if not approved:
            logger.warning(
                "Execution rejected for %s: %s",
                request.match_key,
                verdict.rejection_reason,
            )
            self._record_rejection(request, verdict)
        else:
            logger.info("Execution approved for %s", request.match_key)

        return verdict

    def record_trade(self) -> None:
        """Mark that a trade was just executed (updates trade rate limiter)."""
        self._last_trade_ts = time.monotonic()

    def _get_max_position(self, request: ExecutionRequest) -> float:
        """Return the larger of the two leg positions (conservative)."""
        kalshi_pos = self.data_provider.get_position_size("kalshi", request.kalshi_ticker)
        poly_pos = self.data_provider.get_position_size("polymarket", request.poly_condition_id)
        return max(kalshi_pos, poly_pos)

    def _record_rejection(self, request: ExecutionRequest, verdict: RiskVerdict) -> None:
        if self.event_recorder is None:
            return
        details = json.dumps(
            {
                "match_key": request.match_key,
                "direction": request.direction,
                "size": request.size,
                "price": request.price,
                "failed_checks": [
                    {"limit": c.limit_name, "reason": c.reason} for c in verdict.failed_checks
                ],
            }
        )
        self.event_recorder.record_risk_event(
            event_type="execution_rejected",
            severity="warning",
            details=details,
        )


class InMemoryRiskDataProvider:
    """Simple in-memory implementation for testing and paper trading."""

    def __init__(self) -> None:
        self._positions: dict[tuple[str, str], float] = {}
        self._total_exposure: float = 0.0
        self._daily_pnl: float = 0.0
        self._concurrent_orders: int = 0

    def get_position_size(self, platform: str, ticker: str) -> float:
        return self._positions.get((platform, ticker), 0.0)

    def get_total_exposure(self) -> float:
        return self._total_exposure

    def get_daily_pnl(self) -> float:
        return self._daily_pnl

    def get_concurrent_order_count(self) -> int:
        return self._concurrent_orders

    def set_position(self, platform: str, ticker: str, size: float) -> None:
        self._positions[(platform, ticker)] = size

    def set_total_exposure(self, exposure: float) -> None:
        self._total_exposure = exposure

    def set_daily_pnl(self, pnl: float) -> None:
        self._daily_pnl = pnl

    def set_concurrent_orders(self, count: int) -> None:
        self._concurrent_orders = count


class InMemoryRiskEventRecorder:
    """Collects risk events in a list for testing."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def record_risk_event(
        self,
        event_type: str,
        severity: str,
        details: str,
        execution_id: str | None = None,
    ) -> None:
        self.events.append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "event_type": event_type,
                "severity": severity,
                "details": details,
                "execution_id": execution_id,
            }
        )


class SqliteRiskDataProvider:
    """Queries positions and execution data from SQLite via SQLAlchemy."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def get_position_size(self, platform: str, ticker: str) -> float:
        from sqlalchemy import select

        from polyarb.db.models import positions

        with self._engine.connect() as conn:
            row = conn.execute(
                select(positions.c.quantity)
                .where(positions.c.platform == platform)
                .where(positions.c.ticker == ticker)
                .where(positions.c.closed_at.is_(None))
            ).scalar()
        return float(row) if row is not None else 0.0

    def get_total_exposure(self) -> float:
        from sqlalchemy import func, select

        from polyarb.db.models import positions

        with self._engine.connect() as conn:
            result = conn.execute(
                select(
                    func.coalesce(func.sum(positions.c.quantity * positions.c.avg_price), 0.0)
                ).where(positions.c.closed_at.is_(None))
            ).scalar()
        return float(result)

    def get_daily_pnl(self) -> float:
        from datetime import timedelta

        from sqlalchemy import func, select

        from polyarb.db.models import executions

        cutoff_str = (datetime.now(UTC) - timedelta(hours=24)).isoformat()

        with self._engine.connect() as conn:
            result = conn.execute(
                select(func.coalesce(func.sum(executions.c.profit), 0.0))
                .where(executions.c.completed_at >= cutoff_str)
                .where(executions.c.status == "completed")
            ).scalar()
        return float(result)

    def get_concurrent_order_count(self) -> int:
        from sqlalchemy import func, select

        from polyarb.db.models import execution_legs

        with self._engine.connect() as conn:
            result = conn.execute(
                select(func.count())
                .select_from(execution_legs)
                .where(execution_legs.c.status == "sent")
            ).scalar()
        return int(result)
