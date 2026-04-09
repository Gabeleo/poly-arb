"""Tiered health checks: liveness, readiness, and deep dependency probes."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from polyarb.daemon.state import State


async def check_liveness() -> dict:
    """Always returns {"alive": true}.

    A liveness probe must never depend on external services.
    """
    return {"alive": True}


async def check_readiness(state: State) -> tuple[bool, dict]:
    """Returns (is_ready, detail_dict).

    Ready when at least one scan has completed and the last scan is
    within 2x the scan interval.
    """
    if state.scan_count == 0:
        return False, {"ready": False, "reason": "no scans completed yet"}

    if state.last_scan_at is None:
        return False, {"ready": False, "reason": "no scan timestamp"}

    age = (datetime.now(UTC) - state.last_scan_at).total_seconds()
    max_age = state.config.scan_interval * 2
    if age > max_age:
        return False, {
            "ready": False,
            "reason": f"last scan {age:.0f}s ago (max {max_age:.0f}s)",
        }

    return True, {"ready": True, "scan_count": state.scan_count}


async def _probe_dependency(name: str, coro) -> dict:
    """Run a single dependency probe with a 5-second timeout."""
    start = time.monotonic()
    try:
        await asyncio.wait_for(coro, timeout=5.0)
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"name": name, "status": "ok", "latency_ms": latency_ms}
    except TimeoutError:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"name": name, "status": "degraded", "latency_ms": latency_ms, "error": "timeout"}
    except Exception as exc:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"name": name, "status": "down", "latency_ms": latency_ms, "error": str(exc)}


async def check_deep(  # noqa: C901
    state: State,
    encoder_client: Any = None,
    poly_provider: Any = None,
    kalshi_provider: Any = None,
) -> tuple[bool, dict]:
    """Concurrent health probes to all external dependencies.

    Checks: scan_loop, polymarket_api, kalshi_api, encoder, each with
    status ("ok", "degraded", "down") and latency_ms.
    """
    probes = []

    # Scan loop check (local, not external)
    async def _check_scan_loop():
        if state.scan_count == 0:
            return
        if state.last_scan_at is None:
            raise RuntimeError("no scan timestamp")
        age = (datetime.now(UTC) - state.last_scan_at).total_seconds()
        max_age = state.config.scan_interval * 2 + 10
        if age > max_age:
            raise RuntimeError(f"stale ({age:.0f}s ago)")

    probes.append(_probe_dependency("scan_loop", _check_scan_loop()))

    if poly_provider is not None:

        async def _check_poly():
            # Try a lightweight call; providers expose get_active_markets
            # but we use search_markets with empty query as a health ping
            if hasattr(poly_provider, "health"):
                await poly_provider.health()
            elif hasattr(poly_provider, "search_markets"):
                await poly_provider.search_markets("", limit=1)
            else:
                await poly_provider.get_active_markets()

        probes.append(_probe_dependency("polymarket_api", _check_poly()))

    if kalshi_provider is not None:

        async def _check_kalshi():
            if hasattr(kalshi_provider, "health"):
                await kalshi_provider.health()
            elif hasattr(kalshi_provider, "search_markets"):
                await kalshi_provider.search_markets("", limit=1)
            else:
                await kalshi_provider.get_active_markets()

        probes.append(_probe_dependency("kalshi_api", _check_kalshi()))

    if encoder_client is not None:

        async def _check_encoder():
            ok = await encoder_client.health()
            if not ok:
                raise RuntimeError("encoder health check failed")

        probes.append(_probe_dependency("encoder", _check_encoder()))

    results = await asyncio.gather(*probes)
    checks = {r["name"]: r for r in results}
    healthy = all(r["status"] == "ok" for r in results)

    return healthy, {"healthy": healthy, "checks": checks}
