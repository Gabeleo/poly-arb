"""Starlette REST API + WebSocket push for the daemon."""

from __future__ import annotations

import logging
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket

from polyarb.daemon.routes import (
    execute,
    get_config,
    health,
    match_detail,
    matches,
    opportunities,
    post_config,
    status,
    telegram_webhook,
    ws_endpoint,
)
from polyarb.daemon.state import State

logger = logging.getLogger(__name__)

# Routes that require API key authentication (method, path_prefix)
_PROTECTED_ROUTES: list[tuple[str, str]] = [
    ("POST", "/config"),
    ("POST", "/execute/"),
]


class ApiKeyMiddleware:
    """ASGI middleware that enforces X-API-Key on protected routes and /ws."""

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        self.app = app
        self._api_key = api_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            # Check query param ?api_key= for WebSocket (headers unreliable in browsers)
            qs = scope.get("query_string", b"").decode()
            params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
            if params.get("api_key") != self._api_key:
                ws = WebSocket(scope, receive, send)
                await ws.close(code=4003)
                return
        elif scope["type"] == "http":
            method = scope.get("method", "")
            path = scope.get("path", "")
            if self._is_protected(method, path):
                headers = dict(scope.get("headers", []))
                key = headers.get(b"x-api-key", b"").decode()
                if key != self._api_key:
                    resp = JSONResponse(
                        {"error": "unauthorized"}, status_code=401
                    )
                    await resp(scope, receive, send)
                    return

        await self.app(scope, receive, send)

    def _is_protected(self, method: str, path: str) -> bool:
        for req_method, prefix in _PROTECTED_ROUTES:
            if method == req_method and path.startswith(prefix):
                return True
        return False


def create_app(
    state: State,
    kalshi_client: Any = None,
    lifespan: Any = None,
    approval_manager: Any = None,
    telegram_bot: Any = None,
    api_key: str | None = None,
) -> Starlette:
    """Build and return a Starlette application wired to *state*."""

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/status", status, methods=["GET"]),
        Route("/matches", matches, methods=["GET"]),
        Route("/matches/{id:int}", match_detail, methods=["GET"]),
        Route("/opportunities", opportunities, methods=["GET"]),
        Route("/config", get_config, methods=["GET"]),
        Route("/config", post_config, methods=["POST"]),
        Route("/execute/{id:int}", execute, methods=["POST"]),
        Route("/telegram/webhook", telegram_webhook, methods=["POST"]),
        WebSocketRoute("/ws", ws_endpoint),
    ]

    middleware = []
    if api_key:
        middleware.append(Middleware(ApiKeyMiddleware, api_key=api_key))
        logger.info("API key authentication enabled")

    app = Starlette(routes=routes, lifespan=lifespan, middleware=middleware)

    # Store dependencies on app.state so route handlers can access them
    app.state.daemon_state = state
    app.state.kalshi_client = kalshi_client
    app.state.approval_manager = approval_manager
    app.state.telegram_bot = telegram_bot

    return app
