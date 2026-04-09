"""ASGI middleware for request ID injection and HTTP metrics."""

from __future__ import annotations

import time

from starlette.types import ASGIApp, Receive, Scope, Send

from polyarb.observability.context import new_request_id, request_id_var
from polyarb.observability import metrics


class RequestIdMiddleware:
    """Generates a request_id for every HTTP request and sets it
    in the context var. Also adds X-Request-ID response header."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        rid = new_request_id()

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", rid.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


class MetricsMiddleware:
    """Records HTTP request duration and count per method/route/status.

    Resolves scope["path"] after the response to capture the route pattern
    set by Starlette's routing.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.monotonic()
        status_code = 500

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        await self.app(scope, receive, send_wrapper)

        duration = time.monotonic() - start
        # Use route pattern from Starlette if available, else raw path
        route = scope.get("path", "unknown")
        route_obj = scope.get("route")
        if route_obj is not None and hasattr(route_obj, "path"):
            route = route_obj.path
        method = scope.get("method", "GET")

        metrics.http_request_duration.labels(
            method=method, route=route, status=str(status_code)
        ).observe(duration)
