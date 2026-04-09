"""WebSocket endpoint."""

from __future__ import annotations

from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket


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


routes = [
    WebSocketRoute("/ws", ws_endpoint),
]
