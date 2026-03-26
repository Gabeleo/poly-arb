"""Starlette REST API + WebSocket push for the daemon."""

from __future__ import annotations

import dataclasses
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

from polyarb.daemon.state import State


def create_app(
    state: State,
    kalshi_client: Any = None,
    lifespan: Any = None,
    approval_manager: Any = None,
    telegram_bot: Any = None,
) -> Starlette:
    """Build and return a Starlette application wired to *state*."""

    async def status(request: Request) -> JSONResponse:
        return JSONResponse(state.status_dict())

    async def matches(request: Request) -> JSONResponse:
        return JSONResponse([m.to_dict() for m in state.matches])

    async def match_detail(request: Request) -> JSONResponse:
        try:
            idx = int(request.path_params["id"])
        except (ValueError, KeyError):
            return JSONResponse({"error": "invalid id"}, status_code=400)
        if idx < 1 or idx > len(state.matches):
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(state.matches[idx - 1].to_dict())

    async def opportunities(request: Request) -> JSONResponse:
        return JSONResponse([o.to_dict() for o in state.opportunities])

    async def get_config(request: Request) -> JSONResponse:
        return JSONResponse(dataclasses.asdict(state.config))

    async def post_config(request: Request) -> JSONResponse:
        body = await request.json()
        valid_fields = {f.name for f in dataclasses.fields(state.config)}
        for key in body:
            if key not in valid_fields:
                return JSONResponse(
                    {"error": f"unknown config key: {key}"}, status_code=400
                )

        # Validate value constraints
        _GT_ZERO = {"scan_interval", "order_size", "dedup_window"}
        _GTE_ZERO = {"min_profit"}
        for key, value in body.items():
            if key in _GT_ZERO and value <= 0:
                return JSONResponse(
                    {"error": f"{key} must be > 0"}, status_code=400
                )
            if key in _GTE_ZERO and value < 0:
                return JSONResponse(
                    {"error": f"{key} must be >= 0"}, status_code=400
                )

        for key, value in body.items():
            field_type = type(getattr(state.config, key))
            setattr(state.config, key, field_type(value))
        return JSONResponse(dataclasses.asdict(state.config))

    async def execute(request: Request) -> JSONResponse:
        if kalshi_client is None:
            return JSONResponse(
                {"error": "no kalshi client connected"}, status_code=409
            )
        try:
            idx = int(request.path_params["id"])
        except (ValueError, KeyError):
            return JSONResponse({"error": "invalid id"}, status_code=400)
        if idx < 1 or idx > len(state.matches):
            return JSONResponse({"error": "not found"}, status_code=404)

        match = state.matches[idx - 1]
        profit, kalshi_side, kalshi_desc, poly_desc, kalshi_price = match.best_arb

        price_cents = int(round(kalshi_price * 100))
        count = int(state.config.order_size)
        ticker = match.kalshi_market.condition_id

        result = await kalshi_client.create_order(
            ticker=ticker,
            side=kalshi_side,
            action="buy",
            price_cents=price_cents,
            count=count,
        )
        return JSONResponse({"order": result, "match_id": idx})

    async def telegram_webhook(request: Request) -> JSONResponse:
        body = await request.json()
        callback = body.get("callback_query")
        if not callback or not approval_manager or not telegram_bot:
            return JSONResponse({"ok": True})

        data = callback.get("data", "")
        callback_id = callback.get("id", "")

        if data.startswith("approve:"):
            approval_id = data.split(":", 1)[1]
            await approval_manager.handle_approve(approval_id)
            await telegram_bot.answer_callback(callback_id)
        elif data.startswith("reject:"):
            approval_id = data.split(":", 1)[1]
            await approval_manager.handle_reject(approval_id)
            await telegram_bot.answer_callback(callback_id)

        return JSONResponse({"ok": True})

    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        state.ws_clients.add(websocket)
        try:
            while True:
                await websocket.receive_text()
        except Exception:
            pass
        finally:
            state.ws_clients.discard(websocket)

    routes = [
        Route("/status", status, methods=["GET"]),
        Route("/matches", matches, methods=["GET"]),
        Route("/matches/{id:int}", match_detail, methods=["GET"]),
        Route("/opportunities", opportunities, methods=["GET"]),
        Route("/config", get_config, methods=["GET"]),
        Route("/config", post_config, methods=["POST"]),
        Route("/execute/{id:int}", execute, methods=["POST"]),
        Route("/telegram/webhook", telegram_webhook, methods=["POST"]),
        WebSocketRoute("/ws", ws_endpoint),
    ]

    return Starlette(routes=routes, lifespan=lifespan)
