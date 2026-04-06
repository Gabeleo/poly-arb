"""Market snapshot recorder — fetches both platforms every N seconds and stores to SQLite."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, timezone
from pathlib import Path

from polyarb.data.base import AsyncDataProvider
from polyarb.recorder.db import RecorderDB

log = logging.getLogger(__name__)

DEFAULT_INTERVAL = 30  # seconds
DEFAULT_DB_PATH = "snapshots.db"
# Fetch enough markets to capture everything above the volume floor
DEFAULT_FETCH_LIMIT = 500


async def _safe_fetch(name: str, coro):
    """Run a fetch coroutine, returning [] on failure."""
    try:
        return await coro
    except Exception as e:
        log.warning("fetch %s failed: %s", name, e, exc_info=True)
        return []


async def record_once(
    poly: AsyncDataProvider,
    kalshi: AsyncDataProvider,
    db: RecorderDB,
) -> dict[str, int]:
    """Run a single scan cycle: fetch both platforms, filter, store."""
    scan_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    poly_markets, kalshi_markets = await asyncio.gather(
        _safe_fetch("polymarket", poly.get_active_markets()),
        _safe_fetch("kalshi", kalshi.get_active_markets()),
    )

    poly_count = db.insert_polymarket(scan_ts, poly_markets)
    kalshi_count = db.insert_kalshi(scan_ts, kalshi_markets)

    return {"scan_ts": scan_ts, "polymarket": poly_count, "kalshi": kalshi_count}


async def run_recorder(
    interval: int = DEFAULT_INTERVAL,
    db_path: str | Path = DEFAULT_DB_PATH,
    fetch_limit: int = DEFAULT_FETCH_LIMIT,
) -> None:
    """Main recorder loop. Runs until interrupted."""
    from polyarb.data.async_kalshi import AsyncKalshiDataProvider
    from polyarb.data.async_live import AsyncLiveDataProvider

    db = RecorderDB(db_path)
    poly = AsyncLiveDataProvider(limit=fetch_limit)
    kalshi = AsyncKalshiDataProvider(limit=fetch_limit)

    log.info(
        "recorder started — interval=%ds, db=%s, fetch_limit=%d",
        interval, db_path, fetch_limit,
    )

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _request_stop():
        log.info("shutdown signal received")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_stop)

    try:
        while not stop.is_set():
            t0 = loop.time()
            result = await record_once(poly, kalshi, db)
            log.info(
                "scan %s — poly=%d kalshi=%d",
                result["scan_ts"], result["polymarket"], result["kalshi"],
            )
            elapsed = loop.time() - t0
            delay = max(0, interval - elapsed)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
    except asyncio.CancelledError:
        log.info("recorder cancelled")
    finally:
        await poly.close()
        await kalshi.close()
        db.close()
        log.info("recorder stopped")
