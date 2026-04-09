"""Tests for polyarb.risk — limits, engine, and circuit breaker."""

from __future__ import annotations

import time

from polyarb.risk.circuit_breaker import CircuitBreaker
from polyarb.risk.engine import (
    InMemoryRiskDataProvider,
    InMemoryRiskEventRecorder,
    RiskEngine,
    RiskVerdict,
)
from polyarb.risk.limits import (
    ExecutionRequest,
    RiskLimits,
    check_concurrent_order_limit,
    check_daily_loss_limit,
    check_exposure_limit,
    check_order_size_limit,
    check_position_limit,
    check_trade_rate_limit,
)

# ── Helpers ─────────────────────────────────────────────────


def _request(
    size: float = 10.0,
    price: float = 0.50,
    match_key: str = "poly1:kalshi1",
    kalshi_ticker: str = "kalshi1",
    poly_condition_id: str = "poly1",
    direction: str = "kalshi_yes_poly_no",
) -> ExecutionRequest:
    return ExecutionRequest(
        match_key=match_key,
        kalshi_ticker=kalshi_ticker,
        poly_condition_id=poly_condition_id,
        direction=direction,
        size=size,
        price=price,
    )


def _limits(**overrides) -> RiskLimits:
    return RiskLimits(**overrides)


def _engine(
    limits: RiskLimits | None = None,
    provider: InMemoryRiskDataProvider | None = None,
    recorder: InMemoryRiskEventRecorder | None = None,
) -> RiskEngine:
    return RiskEngine(
        limits=limits or _limits(),
        data_provider=provider or InMemoryRiskDataProvider(),
        event_recorder=recorder,
    )


# ── Individual check function tests ────────────────────────


class TestCheckPositionLimit:
    def test_passes_under_limit(self):
        result = check_position_limit(_request(size=10), _limits(max_position_per_market=50), 30.0)
        assert result.passed

    def test_fails_at_limit(self):
        result = check_position_limit(_request(size=10), _limits(max_position_per_market=50), 41.0)
        assert not result.passed
        assert "51" in result.reason
        assert result.limit_name == "max_position_per_market"

    def test_passes_exactly_at_limit(self):
        result = check_position_limit(_request(size=10), _limits(max_position_per_market=50), 40.0)
        assert result.passed

    def test_fails_zero_headroom(self):
        result = check_position_limit(_request(size=1), _limits(max_position_per_market=50), 50.0)
        assert not result.passed


class TestCheckExposureLimit:
    def test_passes_under_limit(self):
        # 10 contracts * $0.50 = $5 + $100 existing = $105 < $500
        result = check_exposure_limit(
            _request(size=10, price=0.50), _limits(max_total_exposure=500), 100.0
        )
        assert result.passed

    def test_fails_over_limit(self):
        # 10 * $0.50 = $5 + $496 = $501 > $500
        result = check_exposure_limit(
            _request(size=10, price=0.50), _limits(max_total_exposure=500), 496.0
        )
        assert not result.passed
        assert "501.00" in result.reason

    def test_passes_exactly_at_limit(self):
        result = check_exposure_limit(
            _request(size=10, price=0.50), _limits(max_total_exposure=500), 495.0
        )
        assert result.passed


class TestCheckDailyLossLimit:
    def test_passes_no_loss(self):
        result = check_daily_loss_limit(_limits(max_daily_loss=50), 0.0)
        assert result.passed

    def test_passes_under_limit(self):
        result = check_daily_loss_limit(_limits(max_daily_loss=50), -40.0)
        assert result.passed

    def test_fails_over_limit(self):
        result = check_daily_loss_limit(_limits(max_daily_loss=50), -60.0)
        assert not result.passed
        assert "60.00" in result.reason

    def test_passes_with_profit(self):
        result = check_daily_loss_limit(_limits(max_daily_loss=50), 100.0)
        assert result.passed

    def test_fails_exactly_at_limit(self):
        # -50 is not < -50, so it should pass
        result = check_daily_loss_limit(_limits(max_daily_loss=50), -50.0)
        assert result.passed

    def test_fails_just_over_limit(self):
        result = check_daily_loss_limit(_limits(max_daily_loss=50), -50.01)
        assert not result.passed


class TestCheckConcurrentOrderLimit:
    def test_passes_under_limit(self):
        result = check_concurrent_order_limit(_limits(max_concurrent_orders=5), 3)
        assert result.passed

    def test_fails_at_limit(self):
        result = check_concurrent_order_limit(_limits(max_concurrent_orders=5), 5)
        assert not result.passed

    def test_fails_over_limit(self):
        result = check_concurrent_order_limit(_limits(max_concurrent_orders=5), 10)
        assert not result.passed

    def test_passes_with_zero(self):
        result = check_concurrent_order_limit(_limits(max_concurrent_orders=5), 0)
        assert result.passed


class TestCheckOrderSizeLimit:
    def test_passes_under_limit(self):
        result = check_order_size_limit(_request(size=50), _limits(max_single_order_size=100))
        assert result.passed

    def test_fails_over_limit(self):
        result = check_order_size_limit(_request(size=150), _limits(max_single_order_size=100))
        assert not result.passed
        assert "150" in result.reason

    def test_passes_exactly_at_limit(self):
        result = check_order_size_limit(_request(size=100), _limits(max_single_order_size=100))
        assert result.passed


class TestCheckTradeRateLimit:
    def test_passes_no_previous_trade(self):
        result = check_trade_rate_limit(_limits(min_time_between_trades=30), None)
        assert result.passed

    def test_passes_after_cooldown(self):
        now = time.monotonic()
        result = check_trade_rate_limit(
            _limits(min_time_between_trades=30),
            last_trade_ts=now - 31.0,
            now=now,
        )
        assert result.passed

    def test_fails_during_cooldown(self):
        now = time.monotonic()
        result = check_trade_rate_limit(
            _limits(min_time_between_trades=30),
            last_trade_ts=now - 10.0,
            now=now,
        )
        assert not result.passed
        assert "10.0s ago" in result.reason

    def test_passes_exactly_at_boundary(self):
        now = time.monotonic()
        result = check_trade_rate_limit(
            _limits(min_time_between_trades=30),
            last_trade_ts=now - 30.0,
            now=now,
        )
        assert result.passed


# ── RiskEngine integration tests ───────────────────────────


class TestRiskEngine:
    def test_approves_clean_request(self):
        engine = _engine()
        verdict = engine.evaluate(_request())
        assert verdict.approved
        assert len(verdict.failed_checks) == 0

    def test_rejects_oversized_order(self):
        engine = _engine(limits=_limits(max_single_order_size=5))
        verdict = engine.evaluate(_request(size=10))
        assert not verdict.approved
        assert any(c.limit_name == "max_single_order_size" for c in verdict.failed_checks)

    def test_rejects_position_concentration(self):
        provider = InMemoryRiskDataProvider()
        provider.set_position("kalshi", "kalshi1", 45.0)
        engine = _engine(limits=_limits(max_position_per_market=50), provider=provider)
        verdict = engine.evaluate(_request(size=10))
        assert not verdict.approved
        assert any(c.limit_name == "max_position_per_market" for c in verdict.failed_checks)

    def test_rejects_exposure_breach(self):
        provider = InMemoryRiskDataProvider()
        provider.set_total_exposure(490.0)
        engine = _engine(limits=_limits(max_total_exposure=500), provider=provider)
        verdict = engine.evaluate(_request(size=30, price=0.50))
        assert not verdict.approved
        assert any(c.limit_name == "max_total_exposure" for c in verdict.failed_checks)

    def test_rejects_daily_loss_breach(self):
        provider = InMemoryRiskDataProvider()
        provider.set_daily_pnl(-60.0)
        engine = _engine(limits=_limits(max_daily_loss=50), provider=provider)
        verdict = engine.evaluate(_request())
        assert not verdict.approved
        assert any(c.limit_name == "max_daily_loss" for c in verdict.failed_checks)

    def test_rejects_concurrent_orders(self):
        provider = InMemoryRiskDataProvider()
        provider.set_concurrent_orders(5)
        engine = _engine(limits=_limits(max_concurrent_orders=5), provider=provider)
        verdict = engine.evaluate(_request())
        assert not verdict.approved
        assert any(c.limit_name == "max_concurrent_orders" for c in verdict.failed_checks)

    def test_rejects_rapid_fire(self):
        engine = _engine(limits=_limits(min_time_between_trades=30))
        engine.record_trade()  # simulate a trade just happened
        verdict = engine.evaluate(_request())
        assert not verdict.approved
        assert any(c.limit_name == "min_time_between_trades" for c in verdict.failed_checks)

    def test_multiple_failures_all_reported(self):
        provider = InMemoryRiskDataProvider()
        provider.set_daily_pnl(-100.0)
        provider.set_concurrent_orders(10)
        engine = _engine(
            limits=_limits(
                max_daily_loss=50,
                max_concurrent_orders=5,
                max_single_order_size=5,
            ),
            provider=provider,
        )
        verdict = engine.evaluate(_request(size=10))
        assert not verdict.approved
        assert len(verdict.failed_checks) == 3

    def test_records_risk_event_on_rejection(self):
        recorder = InMemoryRiskEventRecorder()
        engine = _engine(
            limits=_limits(max_single_order_size=5),
            recorder=recorder,
        )
        engine.evaluate(_request(size=10))
        assert len(recorder.events) == 1
        assert recorder.events[0]["event_type"] == "execution_rejected"
        assert recorder.events[0]["severity"] == "warning"

    def test_no_risk_event_on_approval(self):
        recorder = InMemoryRiskEventRecorder()
        engine = _engine(recorder=recorder)
        engine.evaluate(_request())
        assert len(recorder.events) == 0

    def test_record_trade_updates_rate_limiter(self):
        engine = _engine(limits=_limits(min_time_between_trades=30))
        # No trade yet — should pass
        assert engine.evaluate(_request()).approved
        engine.record_trade()
        # Right after trade — should fail
        assert not engine.evaluate(_request()).approved

    def test_uses_max_of_two_leg_positions(self):
        """Risk engine should use the larger position of the two legs."""
        provider = InMemoryRiskDataProvider()
        provider.set_position("kalshi", "kalshi1", 10.0)
        provider.set_position("polymarket", "poly1", 45.0)
        engine = _engine(limits=_limits(max_position_per_market=50), provider=provider)
        # Poly side is 45 + 10 = 55 > 50
        verdict = engine.evaluate(_request(size=10))
        assert not verdict.approved


class TestRiskVerdict:
    def test_bool_true_on_approval(self):
        v = RiskVerdict(approved=True)
        assert bool(v) is True

    def test_bool_false_on_rejection(self):
        v = RiskVerdict(approved=False)
        assert bool(v) is False

    def test_rejection_reason_format(self):
        checks = (
            check_order_size_limit(_request(size=200), _limits(max_single_order_size=100)),
            check_concurrent_order_limit(_limits(max_concurrent_orders=5), 10),
        )
        v = RiskVerdict(approved=False, checks=checks)
        reason = v.rejection_reason
        assert "[max_single_order_size]" in reason
        assert "[max_concurrent_orders]" in reason


class TestRiskLimitsFromSettings:
    def test_from_settings(self):
        class FakeSettings:
            max_position_per_market = 25
            max_total_exposure = 1000.0
            max_daily_loss = 100.0
            max_concurrent_orders = 3
            max_single_order_size = 200
            min_time_between_trades = 60.0

        limits = RiskLimits.from_settings(FakeSettings())
        assert limits.max_position_per_market == 25
        assert limits.max_total_exposure == 1000.0
        assert limits.max_daily_loss == 100.0
        assert limits.max_concurrent_orders == 3
        assert limits.max_single_order_size == 200
        assert limits.min_time_between_trades == 60.0


# ── Circuit breaker tests ──────────────────────────────────


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker("test")
        assert cb.is_open is False
        assert cb.backoff_delay == 0.0
        assert cb.failures == 0

    def test_opens_after_threshold(self):
        cb = CircuitBreaker("test", threshold=3)
        for _ in range(3):
            cb.record_failure(RuntimeError("fail"))
        assert cb.is_open is True
        assert cb.backoff_delay > 0

    def test_stays_closed_below_threshold(self):
        cb = CircuitBreaker("test", threshold=5)
        for _ in range(4):
            cb.record_failure(RuntimeError("fail"))
        assert cb.is_open is False

    def test_resets_on_success(self):
        cb = CircuitBreaker("test", threshold=3)
        for _ in range(3):
            cb.record_failure(RuntimeError("fail"))
        assert cb.is_open is True
        cb.record_success()
        assert cb.is_open is False
        assert cb.failures == 0
        assert cb.backoff_delay == 0.0

    def test_exponential_backoff(self):
        cb = CircuitBreaker("test", threshold=2, base_delay=10.0, max_delay=300.0)
        cb.record_failure()
        cb.record_failure()  # now open, failures=2, threshold=2 → 10 * 2^0 = 10
        assert cb.backoff_delay == 10.0
        cb.record_failure()  # failures=3 → 10 * 2^1 = 20
        assert cb.backoff_delay == 20.0
        cb.record_failure()  # failures=4 → 10 * 2^2 = 40
        assert cb.backoff_delay == 40.0

    def test_backoff_capped_at_max(self):
        cb = CircuitBreaker("test", threshold=1, base_delay=10.0, max_delay=50.0)
        for _ in range(20):
            cb.record_failure()
        assert cb.backoff_delay <= 50.0

    def test_reset_force_clears(self):
        cb = CircuitBreaker("test", threshold=2)
        for _ in range(5):
            cb.record_failure()
        cb.reset()
        assert cb.is_open is False
        assert cb.failures == 0

    def test_on_state_change_called_on_open(self):
        events = []
        cb = CircuitBreaker(
            "test",
            threshold=2,
            on_state_change=lambda name, is_open: events.append((name, is_open)),
        )
        cb.record_failure()
        assert len(events) == 0  # not yet at threshold
        cb.record_failure()
        assert events == [("test", True)]

    def test_on_state_change_called_on_close(self):
        events = []
        cb = CircuitBreaker(
            "test",
            threshold=1,
            on_state_change=lambda name, is_open: events.append((name, is_open)),
        )
        cb.record_failure()
        events.clear()
        cb.record_success()
        assert events == [("test", False)]

    def test_no_state_change_on_repeated_failures(self):
        events = []
        cb = CircuitBreaker(
            "test",
            threshold=2,
            on_state_change=lambda name, is_open: events.append((name, is_open)),
        )
        cb.record_failure()
        cb.record_failure()  # opens
        cb.record_failure()  # already open — no new event
        cb.record_failure()
        assert len(events) == 1

    def test_configurable_defaults(self):
        cb = CircuitBreaker("x")
        assert cb.threshold == 5
        assert cb.max_delay == 300.0
        assert cb.base_delay == 10.0

    def test_record_failure_without_exception(self):
        cb = CircuitBreaker("test", threshold=2)
        cb.record_failure()  # no exc arg
        cb.record_failure()
        assert cb.is_open is True
