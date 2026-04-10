"""Structured request/response logging middleware."""

from __future__ import annotations

import json
import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from polyarb.observability.context import request_id_var

logger = logging.getLogger("polyarb.api.access")


class LoggingMiddleware:
    """Emits a structured JSON log entry per HTTP request.

    Fields: method, path, status, duration_ms, request_id, client_ip.
    Placed after RequestIdMiddleware so request_id is available.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.monotonic()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        await self.app(scope, receive, send_wrapper)

        duration_ms = round((time.monotonic() - start) * 1000, 2)
        method = scope.get("method", "GET")
        path = scope.get("path", "/")
        client = scope.get("client")
        client_ip = client[0] if client else "unknown"
        request_id = request_id_var.get("")

        entry = {
            "method": method,
            "path": path,
            "status": status_code,
            "duration_ms": duration_ms,
            "request_id": request_id,
            "client_ip": client_ip,
        }
        logger.info(json.dumps(entry, separators=(",", ":")))
