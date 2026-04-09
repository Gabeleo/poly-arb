"""Config routes — get and update."""

from __future__ import annotations

import dataclasses
import logging

from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from polyarb.api.schemas.requests import ConfigUpdate
from polyarb.config import Config

logger = logging.getLogger(__name__)


async def get_config(request: Request) -> JSONResponse:
    return JSONResponse(dataclasses.asdict(request.app.state.daemon_state.config))


async def update_config(request: Request) -> JSONResponse:
    try:
        body = ConfigUpdate(**(await request.json()))
    except ValidationError as exc:
        return JSONResponse({"error": exc.errors()}, status_code=400)

    st = request.app.state.daemon_state
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return JSONResponse({"error": "no fields to update"}, status_code=400)

    # Save original values for rollback
    original = {key: getattr(st.config, key) for key in updates}

    # Apply updates
    for key, value in updates.items():
        setattr(st.config, key, value)

    # Validate via Config.__post_init__
    try:
        Config(**dataclasses.asdict(st.config))
    except (ValueError, TypeError) as exc:
        # Rollback
        for key, value in original.items():
            setattr(st.config, key, value)
        return JSONResponse({"error": str(exc)}, status_code=400)

    # Audit log
    audit_repo = getattr(request.app.state, "audit_repo", None)
    if audit_repo is not None:
        audit_repo.record("config_update", "api_key", updates)

    return JSONResponse(dataclasses.asdict(st.config))


routes = [
    Route("/config", get_config, methods=["GET"]),
    Route("/config", update_config, methods=["POST"]),
]
