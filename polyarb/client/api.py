"""Synchronous HTTP client for the polyarb daemon REST API."""

from __future__ import annotations

import httpx


class DaemonClient:
    """Thin wrapper around the daemon's REST endpoints."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        client: httpx.Client | None = None,
        api_key: str | None = None,
    ) -> None:
        headers = {"X-API-Key": api_key} if api_key else {}
        self._client = client or httpx.Client(
            base_url=base_url,
            timeout=10.0,
            headers=headers,
        )
        self._owns_client = client is None
        self._api_key = api_key

    # ── helpers ─────────────────────────────────────────────

    def _get(self, path: str) -> dict | list | None:
        resp = self._client.get(path)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, data: dict | None = None) -> dict:
        resp = self._client.post(path, json=data)
        return resp.json()

    # ── public API ─────────────────────────────────────────

    def get_status(self) -> dict:
        return self._get("/status")

    def get_matches(self) -> list[dict]:
        return self._get("/matches")

    def get_match(self, match_id: int) -> dict | None:
        return self._get(f"/matches/{match_id}")

    def get_opportunities(self) -> list[dict]:
        return self._get("/opportunities")

    def execute(self, match_id: int) -> dict:
        """POST /execute/{id} — returns response JSON regardless of HTTP status."""
        return self._post(f"/execute/{match_id}")

    def get_config(self) -> dict:
        return self._get("/config")

    def set_config(self, data: dict) -> dict:
        return self._post("/config", data)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
