"""Health, metrics, and status routes."""

from __future__ import annotations

from datetime import datetime, timezone

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from polyarb.observability.health import check_deep, check_liveness, check_readiness


async def health(request: Request) -> JSONResponse:
    """Health check: probes scan loop liveness."""
    st = request.app.state.daemon_state
    checks: dict[str, str] = {}
    healthy = True

    if st.last_scan_at is not None:
        age = (datetime.now(timezone.utc) - st.last_scan_at).total_seconds()
        max_age = st.config.scan_interval * 2 + 10
        if age > max_age:
            checks["scan_loop"] = f"stale ({age:.0f}s ago)"
            healthy = False
        else:
            checks["scan_loop"] = "ok"
    elif st.scan_count == 0:
        checks["scan_loop"] = "starting"
    else:
        checks["scan_loop"] = "unknown"
        healthy = False

    if st.last_scan_error:
        checks["last_error"] = st.last_scan_error

    status_code = 200 if healthy else 503
    return JSONResponse({"healthy": healthy, "checks": checks}, status_code=status_code)


async def health_live(request: Request) -> JSONResponse:
    """Kubernetes liveness probe — always 200."""
    result = await check_liveness()
    return JSONResponse(result)


async def health_ready(request: Request) -> JSONResponse:
    """Kubernetes readiness probe — 200 after first successful scan."""
    state = request.app.state.daemon_state
    ready, detail = await check_readiness(state)
    status_code = 200 if ready else 503
    return JSONResponse(detail, status_code=status_code)


async def health_deep(request: Request) -> JSONResponse:
    """Deep health check — concurrent probes to all external dependencies."""
    state = request.app.state.daemon_state
    healthy, detail = await check_deep(
        state,
        encoder_client=getattr(request.app.state, "encoder_client", None),
        poly_provider=getattr(request.app.state, "poly_provider", None),
        kalshi_provider=getattr(request.app.state, "kalshi_provider", None),
    )
    status_code = 200 if healthy else 503
    return JSONResponse(detail, status_code=status_code)


async def metrics_endpoint(request: Request) -> Response:
    """Prometheus metrics endpoint — unauthenticated."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def status(request: Request) -> JSONResponse:
    return JSONResponse(request.app.state.daemon_state.status_dict())


routes = [
    Route("/health", health, methods=["GET"]),
    Route("/health/live", health_live, methods=["GET"]),
    Route("/health/ready", health_ready, methods=["GET"]),
    Route("/health/deep", health_deep, methods=["GET"]),
    Route("/metrics", metrics_endpoint, methods=["GET"]),
    Route("/status", status, methods=["GET"]),
]
