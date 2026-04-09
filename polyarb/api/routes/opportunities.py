"""Opportunity routes."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


async def list_opportunities(request: Request) -> JSONResponse:
    return JSONResponse([o.to_dict() for o in request.app.state.daemon_state.opportunities])


routes = [
    Route("/opportunities", list_opportunities, methods=["GET"]),
]
