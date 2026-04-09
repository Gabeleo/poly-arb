"""Tests for polyarb.observability.context — correlation ID context vars."""

from __future__ import annotations

import asyncio
import contextvars

from polyarb.observability.context import (
    new_request_id,
    new_scan_id,
    request_id_var,
    scan_context,
    scan_id_var,
)


def test_new_scan_id_sets_context_var():
    sid = new_scan_id()
    assert scan_id_var.get() == sid
    assert sid != ""


def test_new_request_id_sets_context_var():
    rid = new_request_id()
    assert request_id_var.get() == rid
    assert rid != ""


def test_ids_are_12_characters():
    sid = new_scan_id()
    rid = new_request_id()
    assert len(sid) == 12
    assert len(rid) == 12


def test_scan_context_returns_both():
    sid = new_scan_id()
    rid = new_request_id()
    ctx = scan_context()
    assert ctx == {"scan_id": sid, "request_id": rid}


async def test_context_isolation_across_tasks():
    """Two concurrent asyncio tasks have independent context vars."""
    results = {}

    async def worker(name: str):
        ctx = contextvars.copy_context()
        def _inner():
            sid = new_scan_id()
            results[name] = sid
        ctx.run(_inner)

    t1 = asyncio.create_task(worker("a"))
    t2 = asyncio.create_task(worker("b"))
    await asyncio.gather(t1, t2)

    assert results["a"] != results["b"]
    assert len(results["a"]) == 12
    assert len(results["b"]) == 12
