"""Async client for the cross-encoder scoring service."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class EncoderClient:
    """Calls the cross-encoder sidecar to score market-question pairs.

    Returns ``None`` on any failure so the caller can fall back to
    token-only scoring.
    """

    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(timeout=10.0)
            self._owns_client = True

    async def score_pairs(
        self, pairs: list[tuple[str, str]]
    ) -> list[float] | None:
        """Send pairs to the encoder service and return similarity scores.

        Returns ``None`` on any error (HTTP, connection, malformed response).
        """
        try:
            resp = await self._client.post(
                f"{self._base_url}/score",
                json={"pairs": [list(p) for p in pairs]},
            )
            resp.raise_for_status()
            scores = resp.json()["scores"]
            if len(scores) != len(pairs):
                logger.warning("Encoder returned %d scores for %d pairs", len(scores), len(pairs))
                return None
            return scores
        except Exception:
            logger.warning("Encoder score request failed", exc_info=True)
            return None

    async def health(self) -> bool:
        try:
            resp = await self._client.get(f"{self._base_url}/health")
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
