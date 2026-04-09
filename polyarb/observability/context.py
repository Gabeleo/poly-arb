"""Correlation ID context vars for threading scan_id and request_id through async call chains."""

from __future__ import annotations

import uuid
from contextvars import ContextVar

scan_id_var: ContextVar[str] = ContextVar("scan_id", default="")
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def new_scan_id() -> str:
    """Generate and set a new scan_id. Returns the ID."""
    sid = uuid.uuid4().hex[:12]
    scan_id_var.set(sid)
    return sid


def new_request_id() -> str:
    """Generate and set a new request_id. Returns the ID."""
    rid = uuid.uuid4().hex[:12]
    request_id_var.set(rid)
    return rid


def scan_context() -> dict[str, str]:
    """Return current scan_id and request_id as a dict for logging."""
    return {
        "scan_id": scan_id_var.get(),
        "request_id": request_id_var.get(),
    }


def request_context() -> dict[str, str]:
    """Return current request_id as a dict for logging."""
    return {
        "request_id": request_id_var.get(),
    }
