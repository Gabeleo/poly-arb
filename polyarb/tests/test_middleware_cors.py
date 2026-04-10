"""Tests for CORSMiddleware."""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from polyarb.api.middleware.cors import CORSMiddleware


def _ok(request):
    return JSONResponse({"ok": True})


def _make_app():
    return Starlette(
        routes=[Route("/test", _ok, methods=["GET", "POST"])],
        middleware=[Middleware(CORSMiddleware)],
    )


class TestCORSMiddleware:
    def test_allows_default_origin(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test", headers={"Origin": "http://localhost:3000"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"

    def test_allows_vite_dev_origin(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test", headers={"Origin": "http://localhost:5173"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"

    def test_rejects_unknown_origin(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test", headers={"Origin": "http://evil.com"})
        assert "access-control-allow-origin" not in resp.headers

    def test_preflight_options(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.options(
            "/test",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
        assert "POST" in resp.headers.get("access-control-allow-methods", "")

    def test_credentials_header(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test", headers={"Origin": "http://localhost:3000"})
        assert resp.headers.get("access-control-allow-credentials") == "true"

    def test_custom_origins_from_env(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "https://dashboard.example.com")
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/test", headers={"Origin": "https://dashboard.example.com"}
        )
        assert resp.headers.get("access-control-allow-origin") == "https://dashboard.example.com"
        # Default origin should now be rejected
        resp2 = client.get("/test", headers={"Origin": "http://localhost:3000"})
        assert "access-control-allow-origin" not in resp2.headers

    def test_max_age_header(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.options(
            "/test",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-max-age") == "600"

    def test_request_id_header_allowed(self):
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.options(
            "/test",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Request-ID",
            },
        )
        assert resp.status_code == 200
        allowed = resp.headers.get("access-control-allow-headers", "")
        assert "x-request-id" in allowed.lower()
