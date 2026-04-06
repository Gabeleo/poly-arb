"""Route handlers for the daemon REST API.

Dependencies are accessed via ``request.app.state`` which is populated
by ``create_app`` in ``server.py``.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timezone

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.websockets import WebSocket

from polyarb.config import Config

logger = logging.getLogger(__name__)


async def health(request: Request) -> JSONResponse:
    """Health check: probes scan loop liveness."""
    st = request.app.state.daemon_state
    checks: dict[str, str] = {}
    healthy = True

    # Scan loop liveness: last scan should be within 2× interval
    if st.last_scan_at is not None:
        age = (datetime.now(timezone.utc) - st.last_scan_at).total_seconds()
        max_age = st.config.scan_interval * 2 + 10  # grace period
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


async def status(request: Request) -> JSONResponse:
    return JSONResponse(request.app.state.daemon_state.status_dict())


async def matches(request: Request) -> JSONResponse:
    return JSONResponse([m.to_dict() for m in request.app.state.daemon_state.matches])


async def match_detail(request: Request) -> JSONResponse:
    try:
        idx = int(request.path_params["id"])
    except (ValueError, KeyError):
        return JSONResponse({"error": "invalid id"}, status_code=400)
    st = request.app.state.daemon_state
    if idx < 1 or idx > len(st.matches):
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(st.matches[idx - 1].to_dict())


async def opportunities(request: Request) -> JSONResponse:
    return JSONResponse([o.to_dict() for o in request.app.state.daemon_state.opportunities])


async def get_config(request: Request) -> JSONResponse:
    return JSONResponse(dataclasses.asdict(request.app.state.daemon_state.config))


async def post_config(request: Request) -> JSONResponse:
    body = await request.json()
    st = request.app.state.daemon_state
    valid_fields = {f.name for f in dataclasses.fields(st.config)}
    for key in body:
        if key not in valid_fields:
            return JSONResponse(
                {"error": f"unknown config key: {key}"}, status_code=400
            )

    # Build a trial config to validate via __post_init__
    trial = dataclasses.asdict(st.config)
    for key, value in body.items():
        field_type = type(getattr(st.config, key))
        trial[key] = field_type(value)
    try:
        Config(**trial)
    except (ValueError, TypeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    for key, value in body.items():
        field_type = type(getattr(st.config, key))
        setattr(st.config, key, field_type(value))
    return JSONResponse(dataclasses.asdict(st.config))


async def execute(request: Request) -> JSONResponse:
    # Execution blocked until both Kalshi and Polymarket legs are implemented.
    return JSONResponse(
        {"error": "execution disabled — Polymarket CLOB leg not yet implemented"},
        status_code=503,
    )


async def telegram_webhook(request: Request) -> JSONResponse:
    body = await request.json()
    telegram_bot = request.app.state.telegram_bot
    approval_manager = request.app.state.approval_manager
    st = request.app.state.daemon_state

    if not telegram_bot:
        return JSONResponse({"ok": True})

    # Handle /scan command
    message = body.get("message", {})
    text = (message.get("text") or "").strip()
    if text == "/scan":
        try:
            await telegram_bot.send_digest(st.opportunities)
        except Exception:
            logger.exception("Failed to send /scan digest")
        return JSONResponse({"ok": True})

    # Handle button callbacks
    callback = body.get("callback_query")
    if not callback or not approval_manager:
        return JSONResponse({"ok": True})

    data = callback.get("data", "")
    callback_id = callback.get("id", "")

    try:
        if data.startswith("approve:"):
            approval_id = data.split(":", 1)[1]
            await approval_manager.handle_approve(approval_id)
        elif data.startswith("reject:"):
            approval_id = data.split(":", 1)[1]
            await approval_manager.handle_reject(approval_id)
    except Exception:
        logger.exception("Webhook handler error")
    finally:
        if callback_id:
            try:
                await telegram_bot.answer_callback(callback_id)
            except Exception:
                pass

    return JSONResponse({"ok": True})


async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    ws_clients = websocket.app.state.daemon_state.ws_clients
    ws_clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except Exception:
        pass
    finally:
        ws_clients.discard(websocket)
