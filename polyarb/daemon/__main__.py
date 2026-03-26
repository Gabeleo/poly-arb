"""Daemon entry point: ``python -m polyarb.daemon``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import uvicorn

from polyarb.config import Config
from polyarb.daemon.engine import run_scan_loop
from polyarb.daemon.server import create_app
from polyarb.daemon.state import State
from polyarb.data.async_kalshi import AsyncKalshiDataProvider
from polyarb.data.async_live import AsyncLiveDataProvider

logger = logging.getLogger("polyarb.daemon")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="polyarb daemon")
    p.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8080, help="bind port (default 8080)")
    p.add_argument(
        "--interval", type=float, default=5.0, help="scan interval in seconds (default 5.0)"
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    config = Config(scan_interval=args.interval)
    state = State(config=config)

    poly = AsyncLiveDataProvider()
    kalshi = AsyncKalshiDataProvider()

    # Optional authenticated Kalshi client for execution
    kalshi_client = None
    api_key = os.environ.get("KALSHI_API_KEY")
    key_file = os.environ.get("KALSHI_KEY_FILE")
    if api_key and key_file:
        try:
            from polyarb.execution.async_kalshi import AsyncKalshiClient
            from polyarb.execution.kalshi import KalshiAuth

            auth = KalshiAuth(api_key, key_file)
            kalshi_client = AsyncKalshiClient(auth)
            logger.info("Kalshi execution client configured")
        except Exception as exc:
            logger.warning("Kalshi execution unavailable: %s", exc)

    @asynccontextmanager
    async def lifespan(app):
        # startup
        scan_task = asyncio.get_event_loop().create_task(run_scan_loop(state, poly, kalshi))
        logger.info("Scan loop started (interval=%.1fs)", config.scan_interval)
        yield
        # shutdown
        scan_task.cancel()
        try:
            await scan_task
        except asyncio.CancelledError:
            pass
        await poly.close()
        await kalshi.close()
        if kalshi_client is not None:
            await kalshi_client.close()
        logger.info("Daemon stopped")

    app = create_app(state, kalshi_client=kalshi_client, lifespan=lifespan)

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
