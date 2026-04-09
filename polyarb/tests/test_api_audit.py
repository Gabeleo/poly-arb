"""Tests for audit logging and audit repository."""

from __future__ import annotations

import json
import logging

from sqlalchemy import create_engine

from polyarb.api.audit import AuditLogger
from polyarb.db.models import metadata
from polyarb.db.repositories.audit import SqliteAuditRepository


def _make_repo():
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    return SqliteAuditRepository(engine)


# ── AuditLogger ────────────────────────────────────────────


def test_audit_logger_writes_to_log(caplog):
    logger = AuditLogger()
    with caplog.at_level(logging.INFO, logger="polyarb.audit"):
        logger.record("config_update", "test_actor", {"min_profit": 0.02})
    assert any("AUDIT" in r.message for r in caplog.records)


def test_audit_logger_writes_to_repo():
    repo = _make_repo()
    logger = AuditLogger(repo=repo)
    logger.record("config_update", "test_actor", {"min_profit": 0.02})
    entries = repo.get_entries()
    assert len(entries) == 1
    assert entries[0]["action"] == "config_update"


def test_audit_logger_works_without_repo(caplog):
    logger = AuditLogger()
    with caplog.at_level(logging.INFO, logger="polyarb.audit"):
        logger.record("test_action", "actor", {"key": "value"})
    assert len(caplog.records) >= 1


# ── SqliteAuditRepository ──────────────────────────────────


def test_repo_insert_and_retrieve():
    repo = _make_repo()
    repo.insert_audit_entry({
        "timestamp": "2026-04-08T00:00:00+00:00",
        "actor": "api_key",
        "action": "config_update",
        "details": {"min_profit": 0.02},
        "request_id": "abc123",
    })
    entries = repo.get_entries()
    assert len(entries) == 1
    assert entries[0]["actor"] == "api_key"
    assert entries[0]["request_id"] == "abc123"


def test_repo_filter_by_action():
    repo = _make_repo()
    repo.insert_audit_entry({
        "timestamp": "2026-04-08T00:00:00+00:00",
        "actor": "api_key", "action": "config_update",
        "details": {}, "request_id": None,
    })
    repo.insert_audit_entry({
        "timestamp": "2026-04-08T00:01:00+00:00",
        "actor": "api_key", "action": "execute_approve",
        "details": {}, "request_id": None,
    })
    config_entries = repo.get_entries(action="config_update")
    assert len(config_entries) == 1
    assert config_entries[0]["action"] == "config_update"


def test_repo_limit():
    repo = _make_repo()
    for i in range(5):
        repo.insert_audit_entry({
            "timestamp": f"2026-04-08T00:0{i}:00+00:00",
            "actor": "api_key", "action": "test",
            "details": {"i": i}, "request_id": None,
        })
    entries = repo.get_entries(limit=3)
    assert len(entries) == 3


def test_repo_details_serialized_as_json():
    repo = _make_repo()
    repo.insert_audit_entry({
        "timestamp": "2026-04-08T00:00:00+00:00",
        "actor": "api_key", "action": "config_update",
        "details": {"min_profit": 0.02, "scan_interval": 3.0},
        "request_id": None,
    })
    entries = repo.get_entries()
    details = json.loads(entries[0]["details"])
    assert details["min_profit"] == 0.02
