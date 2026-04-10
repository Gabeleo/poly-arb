"""WebSocket endpoint."""

from __future__ import annotations

import logging

from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    daemon_state = websocket.app.state.daemon_state
    await daemon_state.add_ws_client(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.warning("WebSocket error", exc_info=True)
    finally:
        await daemon_state.remove_ws_client(websocket)


routes = [
    WebSocketRoute("/ws", ws_endpoint),
]
