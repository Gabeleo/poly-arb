"""Telegram webhook route."""

from __future__ import annotations

import contextlib
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from polyarb.observability import metrics

logger = logging.getLogger(__name__)


async def telegram_webhook(request: Request) -> JSONResponse:
    body = await request.json()
    telegram_bot = request.app.state.telegram_bot
    approval_manager = request.app.state.approval_manager
    st = request.app.state.daemon_state

    if not telegram_bot:
        return JSONResponse({"ok": True})

    # Handle /scan command
    message = body.get("message", {})
    text = (message.get("text") or "").strip()
    if text == "/scan":
        try:
            await telegram_bot.send_digest(st.opportunities)
        except Exception:
            logger.exception("Failed to send /scan digest")
        return JSONResponse({"ok": True})

    # Handle button callbacks
    callback = body.get("callback_query")
    if not callback or not approval_manager:
        return JSONResponse({"ok": True})

    data = callback.get("data", "")
    callback_id = callback.get("id", "")

    try:
        if data.startswith("approve:"):
            approval_id = data.split(":", 1)[1]
            await approval_manager.handle_approve(approval_id)
        elif data.startswith("reject:"):
            approval_id = data.split(":", 1)[1]
            await approval_manager.handle_reject(approval_id)
    except Exception:
        logger.exception("Webhook handler error for callback data=%s", data)
        metrics.webhook_errors.inc()
    finally:
        if callback_id:
            with contextlib.suppress(Exception):
                await telegram_bot.answer_callback(callback_id)

    return JSONResponse({"ok": True})


routes = [
    Route("/telegram/webhook", telegram_webhook, methods=["POST"]),
]
