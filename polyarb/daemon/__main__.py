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

    # Optional Telegram notifications
    telegram_bot = None
    approval_manager = None
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if bot_token and chat_id:
        from polyarb.notifications.telegram import TelegramBot
        from polyarb.notifications.approval import ApprovalManager

        telegram_bot = TelegramBot(token=bot_token, chat_id=chat_id)
        approval_manager = ApprovalManager(
            state=state, bot=telegram_bot,
            kalshi_client=kalshi_client, config=config,
        )
        logger.info("Telegram notifications enabled (chat_id=%s)", chat_id)
    else:
        logger.info("Telegram not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")

    # Optional cross-encoder verification
    encoder_client = None
    encoder_url = os.environ.get("ENCODER_URL")
    if encoder_url:
        from polyarb.matching.encoder_client import EncoderClient

        encoder_client = EncoderClient(encoder_url)
        logger.info("Cross-encoder verification enabled (%s)", encoder_url)
    else:
        logger.info("Cross-encoder not configured (set ENCODER_URL)")

    @asynccontextmanager
    async def lifespan(app):
        # startup
        scan_task = asyncio.get_event_loop().create_task(
            run_scan_loop(
                state, poly, kalshi, approval_manager, telegram_bot, encoder_client,
            )
        )
        logger.info("Scan loop started (interval=%.1fs)", config.scan_interval)

        if telegram_bot is not None:
            webhook_url = os.environ.get("TELEGRAM_WEBHOOK_URL", "")
            if webhook_url:
                await telegram_bot.set_webhook(f"{webhook_url}/telegram/webhook")
                logger.info("Telegram webhook registered: %s", webhook_url)

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
        if telegram_bot is not None:
            await telegram_bot.close()
        if encoder_client is not None:
            await encoder_client.close()
        logger.info("Daemon stopped")

    app = create_app(
        state,
        kalshi_client=kalshi_client,
        lifespan=lifespan,
        approval_manager=approval_manager,
        telegram_bot=telegram_bot,
    )

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
