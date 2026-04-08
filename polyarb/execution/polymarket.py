"""Async Polymarket execution client wrapping py-clob-client.

Requires the `py-clob-client` package:
  pip install py-clob-client

Credentials are read from environment variables:
  POLY_PRIVATE_KEY     — hex-encoded wallet private key
  POLY_FUNDER_ADDRESS  — optional proxy/funder wallet address
"""

from __future__ import annotations

import asyncio
import logging

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType

    _HAS_POLY = True
except ImportError:
    _HAS_POLY = False

logger = logging.getLogger(__name__)

POLY_CLOB_URL = "https://clob.polymarket.com"
POLYGON_CHAIN_ID = 137


class AsyncPolymarketClient:
    """Async wrapper around the synchronous py-clob-client SDK.

    All SDK calls are dispatched via ``asyncio.to_thread`` so they never
    block the event loop.
    """

    def __init__(
        self,
        private_key: str,
        funder: str | None = None,
    ) -> None:
        if not _HAS_POLY:
            raise ImportError(
                "py-clob-client is required for Polymarket execution. "
                "Install with: pip install py-clob-client"
            )

        self._client = ClobClient(
            POLY_CLOB_URL,
            key=private_key,
            chain_id=POLYGON_CHAIN_ID,
            signature_type=0,  # EOA
            funder=funder,
        )
        self._client.set_api_creds(self._client.create_or_derive_api_creds())

    async def create_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        order_type: str = "FOK",
    ) -> dict:
        """Place an order on the Polymarket CLOB.

        *side*: ``"BUY"`` or ``"SELL"``
        *price*: 0.00-1.00
        *size*: number of shares
        *order_type*: ``"FOK"`` (default), ``"GTC"``, ``"GTD"``, ``"FAK"``

        Returns the API response dict (contains ``orderID``, ``status``).
        """
        ot_map = {
            "FOK": OrderType.FOK,
            "GTC": OrderType.GTC,
            "GTD": OrderType.GTD,
            "FAK": OrderType.FAK,
        }
        ot = ot_map.get(order_type, OrderType.FOK)

        def _place():
            signed = self._client.create_order(
                OrderArgs(
                    token_id=token_id,
                    price=price,
                    size=size,
                    side=side,
                ),
            )
            return self._client.post_order(signed, ot)

        result = await asyncio.to_thread(_place)
        return result if isinstance(result, dict) else {}

    async def cancel_order(self, order_id: str) -> dict:
        """Cancel an order by ID."""
        result = await asyncio.to_thread(self._client.cancel, order_id)
        return result if isinstance(result, dict) else {}

    async def get_balance(self) -> float:
        """Return available USDC balance (stub — SDK doesn't expose this directly)."""
        return 0.0

    async def close(self) -> None:
        """No persistent connections to clean up."""
        pass
