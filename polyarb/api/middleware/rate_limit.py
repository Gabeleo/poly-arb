"""Token bucket rate limiter — per-client-IP, in-memory."""

from __future__ import annotations

import os
import time

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class _TokenBucket:
    """Token bucket for a single client."""

    def __init__(self, rate: float, burst: int) -> None:
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_refill = time.monotonic()

    def consume(self) -> bool:
        """Try to consume one token. Returns True if allowed."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
        self.last_refill = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    @property
    def retry_after(self) -> float:
        """Seconds until the next token is available."""
        if self.tokens >= 1.0:
            return 0.0
        return (1.0 - self.tokens) / self.rate


_EXEMPT_PREFIXES = ("/health", "/metrics")
_PRUNE_INTERVAL = 100  # prune stale buckets every N requests
_STALE_SECONDS = 300   # remove clients not seen for 5 minutes


class RateLimitMiddleware:
    """ASGI middleware: per-IP token bucket rate limiting.

    Default: 120 req/min (rate=2/s), burst of 20.
    Configurable via RATE_LIMIT_PER_MIN and RATE_LIMIT_BURST env vars.
    Returns 429 with Retry-After header when exceeded.
    Exempts health and metrics endpoints.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        per_min = int(os.environ.get("RATE_LIMIT_PER_MIN", "120"))
        self._burst = int(os.environ.get("RATE_LIMIT_BURST", "20"))
        self._rate = per_min / 60.0  # tokens per second
        self._buckets: dict[str, _TokenBucket] = {}
        self._request_count = 0
        self._trusted_proxies = int(os.environ.get("TRUSTED_PROXY_COUNT", "0"))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        client_ip = self._get_client_ip(scope)
        bucket = self._buckets.get(client_ip)
        if bucket is None:
            bucket = _TokenBucket(self._rate, self._burst)
            self._buckets[client_ip] = bucket

        self._request_count += 1
        if self._request_count % _PRUNE_INTERVAL == 0:
            self._prune_stale()

        if not bucket.consume():
            retry = max(1, int(bucket.retry_after + 0.5))
            resp = JSONResponse(
                {"error": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
            await resp(scope, receive, send)
            return

        await self.app(scope, receive, send)

    def _get_client_ip(self, scope: Scope) -> str:
        """Extract client IP, respecting X-Forwarded-For behind proxies.

        When behind a reverse proxy, X-Forwarded-For contains:
            "client_ip, proxy1, proxy2"
        We take the leftmost (first) entry — the original client IP.
        When TRUSTED_PROXY_COUNT is set, take the Nth-from-right entry
        to defend against spoofed headers.

        Falls back to scope["client"] (TCP peer) when no proxy header
        is present.
        """
        headers = dict(scope.get("headers", []))

        xff = headers.get(b"x-forwarded-for", b"").decode().strip()
        if xff:
            parts = [p.strip() for p in xff.split(",")]
            if self._trusted_proxies > 0 and len(parts) > self._trusted_proxies:
                return parts[-1 - self._trusted_proxies]
            return parts[0]

        xri = headers.get(b"x-real-ip", b"").decode().strip()
        if xri:
            return xri

        client = scope.get("client")
        if client:
            return client[0]

        return "unknown"

    def _prune_stale(self) -> None:
        """Remove buckets not seen for >5 minutes."""
        cutoff = time.monotonic() - _STALE_SECONDS
        stale = [ip for ip, b in self._buckets.items() if b.last_refill < cutoff]
        for ip in stale:
            del self._buckets[ip]
