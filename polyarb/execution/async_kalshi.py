"""Async Kalshi execution client using httpx.

Reuses KalshiAuth (RSA-PSS signing) from polyarb.execution.kalshi.
"""

from __future__ import annotations

import json

import httpx

from polyarb.execution.kalshi import KalshiAuth, KALSHI_DEMO, KALSHI_PROD


class AsyncKalshiClient:
    """Async authenticated HTTP client for the Kalshi Trading API v2."""

    def __init__(
        self,
        auth: KalshiAuth,
        demo: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.auth = auth
        self.base_url = KALSHI_DEMO if demo else KALSHI_PROD
        self.demo = demo
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=15.0,
            )
            self._owns_client = True

    async def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        api_path = f"/trade-api/v2{path}"
        headers = self.auth.headers(method, api_path)

        content = None
        if body is not None:
            content = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        resp = await self._client.request(
            method,
            path,
            content=content,
            headers=headers,
        )
        if not resp.is_success:
            try:
                msg = resp.json().get("message", resp.text)
            except Exception:
                msg = resp.text
            raise RuntimeError(f"Kalshi API {resp.status_code}: {msg}")
        raw = resp.content
        return json.loads(raw) if raw else {}

    # ── Portfolio ───────────────────────────────────────────

    async def get_balance(self) -> float:
        """Return available balance in dollars."""
        data = await self._request("GET", "/portfolio/balance")
        return data.get("balance", 0) / 100.0

    async def get_positions(self, ticker: str = "") -> list[dict]:
        """Return current positions, optionally filtered by ticker."""
        params = f"?limit=100&ticker={ticker}" if ticker else "?limit=100"
        data = await self._request("GET", f"/portfolio/positions{params}")
        return data.get("market_positions", [])

    # ── Orders ──────────────────────────────────────────────

    async def create_order(
        self,
        ticker: str,
        side: str,
        action: str,
        price_cents: int,
        count: int,
        time_in_force: str = "immediate_or_cancel",
    ) -> dict:
        """Place a limit order. Returns the order object.

        *side*: ``"yes"`` or ``"no"``
        *action*: ``"buy"`` or ``"sell"``
        *price_cents*: 1-99 (the price for the given *side*)
        *count*: number of contracts
        """
        price_field = "yes_price" if side == "yes" else "no_price"
        body: dict = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "type": "limit",
            price_field: price_cents,
            "count": count,
            "time_in_force": time_in_force,
        }
        data = await self._request("POST", "/portfolio/orders", body)
        return data.get("order", {})

    async def get_order(self, order_id: str) -> dict:
        data = await self._request("GET", f"/portfolio/orders/{order_id}")
        return data.get("order", {})

    async def cancel_order(self, order_id: str) -> dict:
        return await self._request("DELETE", f"/portfolio/orders/{order_id}")

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
