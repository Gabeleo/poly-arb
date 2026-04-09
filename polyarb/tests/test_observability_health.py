"""Tests for polyarb.observability.health — tiered health checks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from polyarb.config import Config
from polyarb.daemon.state import State
from polyarb.observability.health import check_deep, check_liveness, check_readiness


async def test_liveness_always_returns_alive():
    result = await check_liveness()
    assert result == {"alive": True}


async def test_readiness_after_first_scan():
    state = State(config=Config())
    state.scan_count = 1
    state.last_scan_at = datetime.now(timezone.utc)

    ready, detail = await check_readiness(state)
    assert ready is True
    assert detail["ready"] is True


async def test_readiness_before_first_scan():
    state = State(config=Config())
    assert state.scan_count == 0

    ready, detail = await check_readiness(state)
    assert ready is False
    assert detail["ready"] is False


async def test_readiness_with_stale_scan():
    state = State(config=Config(scan_interval=5.0))
    state.scan_count = 5
    # Last scan was 30 seconds ago, threshold is 2×5=10s
    state.last_scan_at = datetime.now(timezone.utc) - timedelta(seconds=30)

    ready, detail = await check_readiness(state)
    assert ready is False


async def test_deep_health_all_ok():
    state = State(config=Config())
    state.scan_count = 1
    state.last_scan_at = datetime.now(timezone.utc)

    class OkEncoder:
        async def health(self) -> bool:
            return True

    class OkProvider:
        async def search_markets(self, q, limit=1):
            return []

    healthy, detail = await check_deep(
        state,
        encoder_client=OkEncoder(),
        poly_provider=OkProvider(),
        kalshi_provider=OkProvider(),
    )
    assert healthy is True
    assert detail["healthy"] is True
    for check in detail["checks"].values():
        assert check["status"] == "ok"


async def test_deep_health_partial_failure():
    state = State(config=Config())
    state.scan_count = 1
    state.last_scan_at = datetime.now(timezone.utc)

    class FailEncoder:
        async def health(self) -> bool:
            raise RuntimeError("encoder down")

    healthy, detail = await check_deep(
        state,
        encoder_client=FailEncoder(),
    )
    assert healthy is False
    assert detail["checks"]["encoder"]["status"] == "down"
    # scan_loop should still be ok
    assert detail["checks"]["scan_loop"]["status"] == "ok"


async def test_deep_health_timeout():
    state = State(config=Config())
    state.scan_count = 1
    state.last_scan_at = datetime.now(timezone.utc)

    class SlowProvider:
        async def search_markets(self, q, limit=1):
            await asyncio.sleep(10)  # longer than 5s timeout
            return []

    healthy, detail = await check_deep(
        state,
        poly_provider=SlowProvider(),
    )
    assert healthy is False
    assert detail["checks"]["polymarket_api"]["status"] == "degraded"
    # scan_loop should still be ok
    assert detail["checks"]["scan_loop"]["status"] == "ok"
