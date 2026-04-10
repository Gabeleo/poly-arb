"""Starlette app factory — assembles routes, middleware, and state."""

from __future__ import annotations

import logging
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Route

from polyarb.api.middleware.auth import ApiKeyMiddleware
from polyarb.api.middleware.cors import CORSMiddleware
from polyarb.api.middleware.logging import LoggingMiddleware
from polyarb.api.middleware.rate_limit import RateLimitMiddleware
from polyarb.api.openapi import openapi_spec
from polyarb.api.routes import (
    analytics,
    config,
    execution,
    health,
    matches,
    opportunities,
    webhooks,
    ws,
)
from polyarb.daemon.state import State
from polyarb.observability.middleware import MetricsMiddleware, RequestIdMiddleware

logger = logging.getLogger(__name__)


def create_app(
    state: State,
    kalshi_client: Any = None,
    lifespan: Any = None,
    approval_manager: Any = None,
    telegram_bot: Any = None,
    api_key: str | None = None,
    encoder_client: Any = None,
    poly_provider: Any = None,
    kalshi_provider: Any = None,
    audit_repo: Any = None,
    pnl_provider: Any = None,
    performance_provider: Any = None,
) -> Starlette:
    """Build and return a Starlette application wired to *state*."""

    routes = [
        *health.routes,
        *matches.routes,
        *opportunities.routes,
        *config.routes,
        *execution.routes,
        *analytics.routes,
        *webhooks.routes,
        *ws.routes,
        Route("/openapi.json", openapi_spec, methods=["GET"]),
    ]

    middleware = [
        Middleware(CORSMiddleware),  # outermost — handles preflight before anything
        Middleware(RequestIdMiddleware),  # sets request_id for downstream use
        Middleware(LoggingMiddleware),  # structured access log per request
        Middleware(MetricsMiddleware),  # times everything below
        Middleware(RateLimitMiddleware),  # rate check before auth
    ]
    if api_key:
        middleware.append(Middleware(ApiKeyMiddleware, api_key=api_key))
        logger.info("API key authentication enabled")

    app = Starlette(routes=routes, lifespan=lifespan, middleware=middleware)

    # Store dependencies on app.state so route handlers can access them
    app.state.daemon_state = state
    app.state.kalshi_client = kalshi_client
    app.state.approval_manager = approval_manager
    app.state.telegram_bot = telegram_bot
    app.state.encoder_client = encoder_client
    app.state.poly_provider = poly_provider
    app.state.kalshi_provider = kalshi_provider
    app.state.audit_repo = audit_repo
    app.state.pnl_provider = pnl_provider
    app.state.performance_provider = performance_provider

    return app
