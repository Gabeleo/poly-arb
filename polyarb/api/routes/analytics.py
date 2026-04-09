"""Analytics routes — P&L and performance attribution."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


async def pnl(request: Request) -> JSONResponse:
    """GET /analytics/pnl — P&L summary + daily breakdown."""
    provider = request.app.state.pnl_provider
    if provider is None:
        return JSONResponse({"error": "analytics not configured"}, status_code=503)

    lookback = int(request.query_params.get("days", "30"))
    summary = provider.summary().to_dict()
    summary["daily_breakdown"] = [d.to_dict() for d in provider.daily(lookback_days=lookback)]
    summary["positions"] = [p.to_dict() for p in provider.per_pair()]
    return JSONResponse(summary)


async def performance(request: Request) -> JSONResponse:
    """GET /analytics/performance — performance attribution."""
    provider = request.app.state.performance_provider
    if provider is None:
        return JSONResponse({"error": "analytics not configured"}, status_code=503)

    return JSONResponse(provider.summary().to_dict())


routes = [
    Route("/analytics/pnl", pnl, methods=["GET"]),
    Route("/analytics/performance", performance, methods=["GET"]),
]
