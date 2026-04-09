"""API key middleware — enforces X-API-Key on protected routes and /ws."""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket

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
                    resp = JSONResponse({"error": "unauthorized"}, status_code=401)
                    await resp(scope, receive, send)
                    return

        await self.app(scope, receive, send)

    def _is_protected(self, method: str, path: str) -> bool:
        for req_method, prefix in _PROTECTED_ROUTES:
            if method == req_method and path.startswith(prefix):
                return True
        return False
