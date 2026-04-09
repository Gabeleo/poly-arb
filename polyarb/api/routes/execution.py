"""Execution routes — currently a stub until Polymarket CLOB is implemented."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


async def execute(request: Request) -> JSONResponse:
    return JSONResponse(
        {"error": "execution disabled — Polymarket CLOB leg not yet implemented"},
        status_code=503,
    )


routes = [
    Route("/execute/{id:int}", execute, methods=["POST"]),
]
