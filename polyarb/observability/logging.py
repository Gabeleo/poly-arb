"""Structured JSON logging with automatic correlation ID injection."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from polyarb.observability.context import request_id_var, scan_id_var


class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON with correlation context.

    Output fields: timestamp, level, logger, message, scan_id,
    request_id, plus any extra kwargs passed via logger.info("msg", extra={...}).
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "scan_id": scan_id_var.get(),
            "request_id": request_id_var.get(),
        }
        if record.exc_info:
            # exc_info may be True (not yet resolved) or a tuple
            if isinstance(record.exc_info, tuple) and record.exc_info[0] is not None:
                entry["exception"] = self.formatException(record.exc_info)
            elif record.exc_info is True:
                import sys
                exc_info = sys.exc_info()
                if exc_info[0] is not None:
                    entry["exception"] = self.formatException(exc_info)
        # Merge extra fields
        extra = getattr(record, "extra", None)
        if extra and isinstance(extra, dict):
            entry.update(extra)
        return json.dumps(entry, default=str)


class HumanFormatter(logging.Formatter):
    """Human-readable format for local development.

    Format: TIMESTAMP LEVEL LOGGER [scan_id] message
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        sid = scan_id_var.get()
        rid = request_id_var.get()
        ctx = sid or rid
        ctx_str = f" [{ctx}]" if ctx else ""
        msg = record.getMessage()
        line = f"{ts} {record.levelname:<8} {record.name}{ctx_str} {msg}"
        if record.exc_info and record.exc_info[0] is not None:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(json_output: bool = True, level: str = "INFO") -> None:
    """Configure root logger with either JSON or human-readable output.

    Call once at daemon startup, before any logger is used.

    Parameters
    ----------
    json_output:
        True for production (JSON lines to stdout).
        False for local development (human-readable).
    level:
        Log level string ("DEBUG", "INFO", "WARNING", etc.)
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else HumanFormatter())
    root.addHandler(handler)

    # Override uvicorn loggers so they use our formatter
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True
