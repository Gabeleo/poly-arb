"""Match routes — list and detail."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


async def list_matches(request: Request) -> JSONResponse:
    return JSONResponse([m.to_dict() for m in request.app.state.daemon_state.matches])


async def get_match(request: Request) -> JSONResponse:
    try:
        idx = int(request.path_params["id"])
    except (ValueError, KeyError):
        return JSONResponse({"error": "invalid id"}, status_code=400)
    st = request.app.state.daemon_state
    if idx < 1 or idx > len(st.matches):
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(st.matches[idx - 1].to_dict())


routes = [
    Route("/matches", list_matches, methods=["GET"]),
    Route("/matches/{id:int}", get_match, methods=["GET"]),
]
