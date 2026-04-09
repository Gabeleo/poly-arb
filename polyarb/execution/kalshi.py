"""Kalshi execution — authenticated order placement via Trading API v2.

Requires the `cryptography` package for RSA-PSS request signing.
Install with: pip install -e ".[trade]"

Credentials are read from environment variables:
  KALSHI_API_KEY   — your API key ID (from kalshi.com account settings)
  KALSHI_KEY_FILE  — path to the RSA private key PEM file
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
    from cryptography.hazmat.primitives.asymmetric import rsa

    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

try:
    import certifi

    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

_extra_ca = os.environ.get("SSL_CERT_FILE")
if _extra_ca and os.path.isfile(_extra_ca):
    _SSL_CTX.load_verify_locations(_extra_ca)

from polyarb.models import OrderSet  # noqa: E402

KALSHI_PROD = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_DEMO = "https://demo-api.kalshi.co/trade-api/v2"


# ── Authentication ──────────────────────────────────────────


class KalshiAuth:
    """RSA-PSS signing for Kalshi API requests."""

    def __init__(self, api_key_id: str, private_key_path: str) -> None:
        if not _HAS_CRYPTO:
            raise ImportError(
                "cryptography package required for Kalshi execution. "
                "Install with: pip install cryptography"
            )
        self.api_key_id = api_key_id
        with open(private_key_path, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise TypeError("Kalshi requires an RSA private key")
        self._private_key = key

    def headers(self, method: str, path: str) -> dict[str, str]:
        """Build signed headers for a Kalshi API request.

        *path* is the full API path (e.g. ``/trade-api/v2/portfolio/balance``),
        without query parameters.
        """
        timestamp_ms = str(int(time.time() * 1000))
        path_clean = path.split("?")[0]
        message = f"{timestamp_ms}{method}{path_clean}".encode()

        signature = self._private_key.sign(
            message,
            asym_padding.PSS(
                mgf=asym_padding.MGF1(hashes.SHA256()),
                salt_length=asym_padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )

        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "polyarb/0.1",
        }


# ── HTTP Client ─────────────────────────────────────────────


class KalshiClient:
    """Authenticated HTTP client for the Kalshi Trading API v2."""

    def __init__(self, auth: KalshiAuth, demo: bool = True) -> None:
        self.auth = auth
        self.base_url = KALSHI_DEMO if demo else KALSHI_PROD
        self.demo = demo

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        api_path = f"/trade-api/v2{path}"
        headers = self.auth.headers(method, api_path)

        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            try:
                msg = json.loads(err_body).get("message", err_body)
            except json.JSONDecodeError:
                msg = err_body
            raise RuntimeError(f"Kalshi API {e.code}: {msg}") from e

    # ── Portfolio ───────────────────────────────────────────

    def get_balance(self) -> float:
        """Return available balance in dollars."""
        data = self._request("GET", "/portfolio/balance")
        return data.get("balance", 0) / 100.0

    def get_positions(self, ticker: str = "") -> list[dict]:
        """Return current positions, optionally filtered by ticker."""
        params: dict[str, str] = {"limit": "100"}
        if ticker:
            params["ticker"] = ticker
        qs = urllib.parse.urlencode(params)
        data = self._request("GET", f"/portfolio/positions?{qs}")
        return data.get("market_positions", [])

    # ── Orders ──────────────────────────────────────────────

    def create_order(
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
        data = self._request("POST", "/portfolio/orders", body)
        return data.get("order", {})

    def get_order(self, order_id: str) -> dict:
        data = self._request("GET", f"/portfolio/orders/{order_id}")
        return data.get("order", {})

    def cancel_order(self, order_id: str) -> dict:
        return self._request("DELETE", f"/portfolio/orders/{order_id}")


# ── Executor ────────────────────────────────────────────────


@dataclass
class KalshiExecutor:
    """Execute OrderSets on Kalshi.

    Implements the same interface as MockExecutor (trades, total_profit,
    execute) so the CLI portfolio view works unchanged.
    """

    client: KalshiClient
    trades: list[OrderSet] = field(default_factory=list)
    total_profit: float = 0.0

    def execute(self, order_set: OrderSet) -> bool:
        """Place all orders in the set using immediate-or-cancel.

        Returns True if every order was accepted (may be partial fills).
        """
        balance = self.client.get_balance()
        if balance < order_set.total_cost:
            print(
                f"\033[91m  \u2717 Insufficient balance: "
                f"${balance:.2f} < ${order_set.total_cost:.2f}\033[0m"
            )
            return False

        env = "DEMO" if self.client.demo else "PRODUCTION"
        print(f"\033[93m  Placing {len(order_set.orders)} orders on Kalshi ({env})...\033[0m")

        placed_order_ids: list[str] = []
        failed = False

        for order in order_set.orders:
            ticker = order.token_id.rsplit(":", 1)[0]
            side = order.side.value.lower()
            action = order.action.value.lower()
            price_cents = max(1, min(99, round(order.price * 100)))
            count = max(1, int(order.size))

            try:
                result = self.client.create_order(
                    ticker=ticker,
                    side=side,
                    action=action,
                    price_cents=price_cents,
                    count=count,
                )
                status = result.get("status", "unknown")
                filled = result.get("fill_count_fp", "0")
                remaining = result.get("remaining_count_fp", "?")
                order_id = result.get("order_id", "")
                if order_id:
                    placed_order_ids.append(order_id)
                print(
                    f"    {action.upper()} {count}x {side.upper()} "
                    f"{ticker} @ ${order.price:.2f} "
                    f"\u2192 {status} (filled={filled}, remaining={remaining})"
                )
            except Exception as e:
                print(f"\033[91m    \u2717 {action.upper()} {side.upper()} {ticker}: {e}\033[0m")
                failed = True
                break  # Stop placing further legs

        if failed and placed_order_ids:
            print("\033[91m  Cancelling previously placed orders...\033[0m")
            for oid in placed_order_ids:
                try:
                    self.client.cancel_order(oid)
                    print(f"    Cancelled {oid}")
                except Exception as e:
                    print(f"\033[91m    Cancel {oid} failed: {e} — check manually\033[0m")

        if failed:
            print(
                "\033[91m  \u26a0 Order set aborted. Check positions for any partial fills.\033[0m"
            )
            return False

        self.trades.append(order_set)
        self.total_profit += order_set.expected_profit
        print(
            f"\033[92m  \u2713 All orders placed. "
            f"Expected profit: ${order_set.expected_profit:.4f}\033[0m"
        )
        return True
