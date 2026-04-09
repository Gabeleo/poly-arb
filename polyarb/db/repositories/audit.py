"""Audit log repository."""

from __future__ import annotations

import json
from typing import Protocol

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from polyarb.db.models import audit_log


class AuditRepository(Protocol):
    def insert_audit_entry(self, entry: dict) -> None: ...
    def get_entries(self, limit: int = 100, action: str | None = None) -> list[dict]: ...


class SqliteAuditRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def insert_audit_entry(self, entry: dict) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(audit_log).values(
                    timestamp=entry["timestamp"],
                    actor=entry["actor"],
                    action=entry["action"],
                    details=json.dumps(entry["details"]),
                    request_id=entry.get("request_id"),
                )
            )

    def get_entries(self, limit: int = 100, action: str | None = None) -> list[dict]:
        stmt = select(audit_log).order_by(audit_log.c.id.desc()).limit(limit)
        if action:
            stmt = stmt.where(audit_log.c.action == action)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [dict(r) for r in rows]
