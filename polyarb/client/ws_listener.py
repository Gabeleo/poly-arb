"""Background WebSocket listener for real-time daemon push messages."""

from __future__ import annotations

import json
import threading
import time
from typing import Callable


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
                            continue
                        on_message(data)
            except Exception:
                time.sleep(2)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
