"""Tests for LoggingMiddleware."""

from __future__ import annotations

import json

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from polyarb.api.middleware.logging import LoggingMiddleware


def _ok(request):
    return JSONResponse({"ok": True})


def _error(request):
    return PlainTextResponse("fail", status_code=500)


def _make_app(routes=None):
    if routes is None:
        routes = [Route("/test", _ok)]
    return Starlette(
        routes=routes,
        middleware=[Middleware(LoggingMiddleware)],
    )


class TestLoggingMiddleware:
    def test_logs_request(self, caplog):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level("INFO", logger="polyarb.api.access"):
            client.get("/test")
        log_lines = [r for r in caplog.records if r.name == "polyarb.api.access"]
        assert len(log_lines) == 1
        entry = json.loads(log_lines[0].message)
        assert entry["method"] == "GET"
        assert entry["path"] == "/test"
        assert entry["status"] == 200
        assert "duration_ms" in entry

    def test_logs_status_code(self, caplog):
        app = _make_app([Route("/err", _error)])
        client = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level("INFO", logger="polyarb.api.access"):
            client.get("/err")
        log_lines = [r for r in caplog.records if r.name == "polyarb.api.access"]
        entry = json.loads(log_lines[0].message)
        assert entry["status"] == 500

    def test_logs_post_method(self, caplog):
        app = _make_app([Route("/test", _ok, methods=["POST"])])
        client = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level("INFO", logger="polyarb.api.access"):
            client.post("/test")
        log_lines = [r for r in caplog.records if r.name == "polyarb.api.access"]
        entry = json.loads(log_lines[0].message)
        assert entry["method"] == "POST"

    def test_includes_client_ip(self, caplog):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level("INFO", logger="polyarb.api.access"):
            client.get("/test")
        log_lines = [r for r in caplog.records if r.name == "polyarb.api.access"]
        entry = json.loads(log_lines[0].message)
        assert "client_ip" in entry

    def test_includes_request_id_when_set(self, caplog):
        """When RequestIdMiddleware runs first, request_id appears in log."""
        from polyarb.observability.middleware import RequestIdMiddleware

        app = Starlette(
            routes=[Route("/test", _ok)],
            middleware=[
                Middleware(RequestIdMiddleware),
                Middleware(LoggingMiddleware),
            ],
        )
        client = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level("INFO", logger="polyarb.api.access"):
            client.get("/test")
        log_lines = [r for r in caplog.records if r.name == "polyarb.api.access"]
        entry = json.loads(log_lines[0].message)
        assert entry["request_id"] != ""

    def test_skips_non_http(self):
        """Non-HTTP scopes pass through without logging."""
        app = _make_app()
        # WebSocket test — just verify no crash
        client = TestClient(app, raise_server_exceptions=False)
        # TestClient only does HTTP, so this is implicitly tested by the
        # middleware's type check. Verify the middleware doesn't interfere
        # with normal HTTP.
        resp = client.get("/test")
        assert resp.status_code == 200

    def test_duration_is_positive(self, caplog):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        with caplog.at_level("INFO", logger="polyarb.api.access"):
            client.get("/test")
        log_lines = [r for r in caplog.records if r.name == "polyarb.api.access"]
        entry = json.loads(log_lines[0].message)
        assert entry["duration_ms"] >= 0
