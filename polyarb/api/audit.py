"""Append-only audit log for state-mutating actions."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from polyarb.observability.context import request_id_var

logger = logging.getLogger("polyarb.audit")


class AuditLogger:
    """Writes audit entries to structured log and optionally to the database."""

    def __init__(self, repo=None) -> None:
        self._repo = repo

    def record(self, action: str, actor: str, details: dict) -> None:
        """Log an audit entry to structured logging and optionally to database."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "action": action,
            "details": details,
            "request_id": request_id_var.get(),
        }
        logger.info("AUDIT: %s", json.dumps(entry))
        if self._repo is not None:
            self._repo.insert_audit_entry(entry)
