"""CORS middleware — wraps Starlette's CORSMiddleware with project defaults."""

from __future__ import annotations

import os

from starlette.middleware.cors import CORSMiddleware as _StarletteCORS
from starlette.types import ASGIApp, Receive, Scope, Send

# Sensible defaults for a dashboard/API consumer setup.
_DEFAULT_ORIGINS = "http://localhost:3000,http://localhost:5173"
_DEFAULT_METHODS = "GET,POST,PUT,DELETE,OPTIONS"
_DEFAULT_HEADERS = "Authorization,Content-Type,X-Request-ID"


class CORSMiddleware:
    """Project-level CORS wrapper.

    Reads allowed origins from ``CORS_ORIGINS`` env var (comma-separated).
    Falls back to localhost dev origins when unset.
    """

    def __init__(self, app: ASGIApp) -> None:
        origins_csv = os.environ.get("CORS_ORIGINS", _DEFAULT_ORIGINS)
        origins = [o.strip() for o in origins_csv.split(",") if o.strip()]

        methods_csv = os.environ.get("CORS_METHODS", _DEFAULT_METHODS)
        methods = [m.strip() for m in methods_csv.split(",") if m.strip()]

        headers_csv = os.environ.get("CORS_HEADERS", _DEFAULT_HEADERS)
        headers = [h.strip() for h in headers_csv.split(",") if h.strip()]

        self._cors = _StarletteCORS(
            app,
            allow_origins=origins,
            allow_methods=methods,
            allow_headers=headers,
            allow_credentials=True,
            max_age=600,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._cors(scope, receive, send)
