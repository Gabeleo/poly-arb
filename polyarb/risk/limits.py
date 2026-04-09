"""Risk limit definitions and evaluation.

Each limit is a pure function: takes the current state and returns a
RiskCheckResult.  The RiskEngine (engine.py) coordinates data fetching
and chains the checks.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    """Deploy-time risk limits.  Constructed from Settings at startup."""

    max_position_per_market: int = 50
    max_total_exposure: float = 500.0
    max_daily_loss: float = 50.0
    max_concurrent_orders: int = 5
    max_single_order_size: int = 100
    min_time_between_trades: float = 30.0

    @classmethod
    def from_settings(cls, settings) -> RiskLimits:
        return cls(
            max_position_per_market=settings.max_position_per_market,
            max_total_exposure=settings.max_total_exposure,
            max_daily_loss=settings.max_daily_loss,
            max_concurrent_orders=settings.max_concurrent_orders,
            max_single_order_size=settings.max_single_order_size,
            min_time_between_trades=settings.min_time_between_trades,
        )


@dataclass(frozen=True)
class ExecutionRequest:
    """Parameters for a proposed trade, validated by the risk engine."""

    match_key: str  # "poly_cid:kalshi_ticker"
    kalshi_ticker: str
    poly_condition_id: str
    direction: str  # e.g. "kalshi_yes_poly_no"
    size: float  # contracts
    price: float  # cost per contract (used for exposure calc)


@dataclass(frozen=True)
class RiskCheckResult:
    """Outcome of a single risk check."""

    passed: bool
    limit_name: str
    reason: str = ""

    def __bool__(self) -> bool:
        return self.passed


# ── Individual check functions ───────────────────────────────────


def check_position_limit(
    request: ExecutionRequest,
    limits: RiskLimits,
    current_position: float,
) -> RiskCheckResult:
    """Reject if adding *size* contracts would exceed per-market cap."""
    projected = current_position + request.size
    if projected > limits.max_position_per_market:
        return RiskCheckResult(
            passed=False,
            limit_name="max_position_per_market",
            reason=(
                f"Position would be {projected:.0f} contracts "
                f"(limit {limits.max_position_per_market})"
            ),
        )
    return RiskCheckResult(passed=True, limit_name="max_position_per_market")


def check_exposure_limit(
    request: ExecutionRequest,
    limits: RiskLimits,
    current_exposure: float,
) -> RiskCheckResult:
    """Reject if total capital at risk would exceed cap."""
    additional = request.size * request.price
    projected = current_exposure + additional
    if projected > limits.max_total_exposure:
        return RiskCheckResult(
            passed=False,
            limit_name="max_total_exposure",
            reason=(f"Exposure would be ${projected:.2f} (limit ${limits.max_total_exposure:.2f})"),
        )
    return RiskCheckResult(passed=True, limit_name="max_total_exposure")


def check_daily_loss_limit(
    limits: RiskLimits,
    daily_loss: float,
) -> RiskCheckResult:
    """Reject if rolling 24h loss has exceeded the cap.

    *daily_loss* is a negative number representing realized + unrealized
    loss in the last 24 hours.  A value of -60 means $60 lost.
    """
    if daily_loss < -limits.max_daily_loss:
        return RiskCheckResult(
            passed=False,
            limit_name="max_daily_loss",
            reason=(f"Daily loss is ${abs(daily_loss):.2f} (limit ${limits.max_daily_loss:.2f})"),
        )
    return RiskCheckResult(passed=True, limit_name="max_daily_loss")


def check_concurrent_order_limit(
    limits: RiskLimits,
    concurrent_orders: int,
) -> RiskCheckResult:
    """Reject if too many orders are already in-flight."""
    if concurrent_orders >= limits.max_concurrent_orders:
        return RiskCheckResult(
            passed=False,
            limit_name="max_concurrent_orders",
            reason=(f"{concurrent_orders} orders in-flight (limit {limits.max_concurrent_orders})"),
        )
    return RiskCheckResult(passed=True, limit_name="max_concurrent_orders")


def check_order_size_limit(
    request: ExecutionRequest,
    limits: RiskLimits,
) -> RiskCheckResult:
    """Reject if individual order size exceeds sanity cap."""
    if request.size > limits.max_single_order_size:
        return RiskCheckResult(
            passed=False,
            limit_name="max_single_order_size",
            reason=(
                f"Order size {request.size:.0f} contracts (limit {limits.max_single_order_size})"
            ),
        )
    return RiskCheckResult(passed=True, limit_name="max_single_order_size")


def check_trade_rate_limit(
    limits: RiskLimits,
    last_trade_ts: float | None,
    now: float | None = None,
) -> RiskCheckResult:
    """Reject if the last trade was too recent (prevents rapid-fire)."""
    if last_trade_ts is None:
        return RiskCheckResult(passed=True, limit_name="min_time_between_trades")
    if now is None:
        now = time.monotonic()
    elapsed = now - last_trade_ts
    if elapsed < limits.min_time_between_trades:
        remaining = limits.min_time_between_trades - elapsed
        return RiskCheckResult(
            passed=False,
            limit_name="min_time_between_trades",
            reason=(
                f"Last trade {elapsed:.1f}s ago "
                f"(minimum {limits.min_time_between_trades:.0f}s, "
                f"wait {remaining:.1f}s)"
            ),
        )
    return RiskCheckResult(passed=True, limit_name="min_time_between_trades")
