"""Daemon entry point: ``python -m polyarb.daemon``."""

from __future__ import annotations

import argparse
import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn

from polyarb.config import Config, Settings
from polyarb.daemon.engine import FETCH_TIMEOUT, run_scan_loop
from polyarb.api.app import create_app
from polyarb.daemon.state import State
from polyarb.data.async_kalshi import AsyncKalshiDataProvider
from polyarb.data.async_live import AsyncLiveDataProvider
from polyarb.observability.logging import configure_logging

logger = logging.getLogger("polyarb.daemon")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="polyarb daemon")
    p.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8080, help="bind port (default 8080)")
    p.add_argument(
        "--interval", type=float, default=5.0, help="scan interval in seconds (default 5.0)"
    )
    p.add_argument(
        "--log-json", action="store_true", default=True, help="emit JSON logs (default)"
    )
    p.add_argument(
        "--no-log-json", dest="log_json", action="store_false", help="emit human-readable logs"
    )
    p.add_argument(
        "--log-level", default=None, help="log level (overrides POLYARB_LOG_LEVEL)"
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    settings = Settings()

    # CLI args override settings for log level
    log_level = args.log_level or settings.log_level
    log_json = settings.log_format == "json" and args.log_json
    configure_logging(json_output=log_json, level=log_level)

    # Database setup
    from polyarb.api.audit import AuditLogger
    from polyarb.db.engine import create_engine as create_db_engine
    from polyarb.db.models import metadata
    from polyarb.db.repositories.audit import SqliteAuditRepository
    from polyarb.db.repositories.matches import SqliteMatchSnapshotRepository

    db_engine = create_db_engine(settings.database_url)
    metadata.create_all(db_engine)
    match_repo = SqliteMatchSnapshotRepository(db_engine)
    audit_repo = SqliteAuditRepository(db_engine)
    audit_logger = AuditLogger(repo=audit_repo)
    logger.info("Database: %s", settings.database_url)

    config = Config(scan_interval=args.interval)
    state = State(config=config)

    poly = AsyncLiveDataProvider()
    kalshi = AsyncKalshiDataProvider()

    # Optional authenticated Kalshi client for execution
    kalshi_client = None
    if settings.kalshi_api_key and settings.kalshi_key_file:
        try:
            from polyarb.execution.async_kalshi import AsyncKalshiClient
            from polyarb.execution.kalshi import KalshiAuth

            auth = KalshiAuth(settings.kalshi_api_key, settings.kalshi_key_file)
            kalshi_client = AsyncKalshiClient(auth)
            logger.info("Kalshi execution client configured")
        except Exception as exc:
            logger.warning("Kalshi execution unavailable: %s", exc)

    # Optional Telegram notifications
    telegram_bot = None
    approval_manager = None
    if settings.telegram_bot_token and settings.telegram_chat_id:
        from polyarb.notifications.telegram import TelegramBot
        from polyarb.notifications.approval import ApprovalManager

        telegram_bot = TelegramBot(
            token=settings.telegram_bot_token, chat_id=settings.telegram_chat_id,
        )
        approval_manager = ApprovalManager(
            state=state, bot=telegram_bot,
            kalshi_client=kalshi_client, config=config,
        )
        logger.info("Telegram notifications enabled (chat_id=%s)", settings.telegram_chat_id)
    else:
        logger.info("Telegram not configured (set POLYARB_TELEGRAM_BOT_TOKEN and POLYARB_TELEGRAM_CHAT_ID)")

    # Optional cross-encoder verification
    encoder_client = None
    if settings.encoder_url:
        from polyarb.matching.encoder_client import EncoderClient

        encoder_client = EncoderClient(settings.encoder_url)
        logger.info("Cross-encoder verification enabled (%s)", settings.encoder_url)
    else:
        logger.info("Cross-encoder not configured (set POLYARB_ENCODER_URL)")

    # Optional bi-encoder pre-filter (local sentence embeddings)
    biencoder = None
    try:
        from polyarb.matching.biencoder import BiEncoderFilter

        biencoder = BiEncoderFilter()
        logger.info("Bi-encoder filter loaded (all-MiniLM-L6-v2)")
    except ImportError:
        logger.info("sentence-transformers not available, bi-encoder disabled")
    state.biencoder_enabled = biencoder is not None

    stop_event = asyncio.Event()

    @asynccontextmanager
    async def lifespan(app):
        # startup
        scan_task = asyncio.get_event_loop().create_task(
            run_scan_loop(
                state, poly, kalshi, approval_manager, telegram_bot, encoder_client,
                stop_event=stop_event, biencoder=biencoder,
                match_repo=match_repo,
            )
        )
        logger.info("Scan loop started (interval=%.1fs)", config.scan_interval)

        if telegram_bot is not None and settings.telegram_webhook_url:
            await telegram_bot.set_webhook(f"{settings.telegram_webhook_url}/telegram/webhook")
            logger.info("Telegram webhook registered: %s", settings.telegram_webhook_url)

        yield

        # Graceful shutdown: signal the loop to finish its current scan
        logger.info("Shutting down — waiting for current scan to finish...")
        stop_event.set()
        try:
            await asyncio.wait_for(scan_task, timeout=FETCH_TIMEOUT + 10)
        except asyncio.TimeoutError:
            logger.warning("Scan did not finish in time, cancelling")
            scan_task.cancel()
            try:
                await scan_task
            except asyncio.CancelledError:
                pass
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

    if settings.api_key:
        logger.info("API key authentication enabled")
    else:
        logger.warning("POLYARB_API_KEY not set — protected endpoints are unauthenticated")

    app = create_app(
        state,
        kalshi_client=kalshi_client,
        lifespan=lifespan,
        approval_manager=approval_manager,
        telegram_bot=telegram_bot,
        api_key=settings.api_key or None,
        encoder_client=encoder_client,
        poly_provider=poly,
        kalshi_provider=kalshi,
        audit_repo=audit_logger,
    )

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", log_config=None)


if __name__ == "__main__":
    main()
