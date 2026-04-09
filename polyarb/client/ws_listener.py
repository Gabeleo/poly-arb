"""Background WebSocket listener for real-time daemon push messages."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)


def start_ws_listener(
    url: str,
    on_message: Callable[[dict], None],
) -> threading.Thread:
    """Connect to the daemon WS endpoint in a daemon thread.

    Calls *on_message* for each JSON message received.  On disconnect or
    error, sleeps 2 seconds and reconnects.  Returns the thread (already
    started).
    """

    def _run() -> None:
        from websockets.sync.client import connect

        while True:
            try:
                with connect(url) as ws:
                    for raw in ws:
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            logger.debug("Malformed JSON from WS: %s", raw[:200])
                            continue
                        try:
                            on_message(data)
                        except Exception:
                            logger.exception("WS message handler error")
            except Exception as exc:
                logger.debug("WS connection lost (%s), reconnecting in 2s", exc)
                time.sleep(2)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
