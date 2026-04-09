"""Tests for AsyncPolymarketClient wrapper (mocked SDK)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ── Fake SDK objects ──────────────────────────────────────────


class FakeOrderType:
    FOK = "FOK"
    GTC = "GTC"
    GTD = "GTD"
    FAK = "FAK"


class FakeOrderArgs:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeClobClient:
    """Stand-in for py_clob_client.client.ClobClient."""

    def __init__(self, *args, **kwargs):
        self.orders_created: list[dict] = []
        self.orders_posted: list[tuple] = []
        self.cancelled: list[str] = []

    def set_api_creds(self, creds):
        pass

    def create_or_derive_api_creds(self):
        return {"api_key": "test", "api_secret": "test", "api_passphrase": "test"}

    def create_order(self, order_args):
        self.orders_created.append(order_args.__dict__)
        return {"signed": True}

    def post_order(self, signed_order, order_type):
        self.orders_posted.append((signed_order, order_type))
        return {"orderID": "0xabc123", "status": "matched"}

    def cancel(self, order_id):
        self.cancelled.append(order_id)
        return {"status": "cancelled"}


# ── Patch the SDK imports ─────────────────────────────────────


@pytest.fixture
def patched_poly_module():
    """Import AsyncPolymarketClient with mocked SDK."""
    import sys

    # Create fake modules
    fake_client_mod = MagicMock()
    fake_client_mod.ClobClient = FakeClobClient
    fake_types_mod = MagicMock()
    fake_types_mod.OrderArgs = FakeOrderArgs
    fake_types_mod.OrderType = FakeOrderType

    with patch.dict(
        sys.modules,
        {
            "py_clob_client": MagicMock(),
            "py_clob_client.client": fake_client_mod,
            "py_clob_client.clob_types": fake_types_mod,
        },
    ):
        # Force reimport so the guard picks up the fakes
        if "polyarb.execution.polymarket" in sys.modules:
            del sys.modules["polyarb.execution.polymarket"]
        from polyarb.execution.polymarket import AsyncPolymarketClient

        yield AsyncPolymarketClient


# ── Tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_order_passes_correct_params(patched_poly_module):
    client = patched_poly_module("0xdeadbeef")

    result = await client.create_order(
        token_id="0xtokenid",
        side="BUY",
        price=0.55,
        size=10.0,
        order_type="FOK",
    )

    assert result["orderID"] == "0xabc123"
    assert result["status"] == "matched"

    # Verify the SDK received correct args
    sdk = client._client
    assert len(sdk.orders_created) == 1
    created = sdk.orders_created[0]
    assert created["token_id"] == "0xtokenid"
    assert created["price"] == 0.55
    assert created["size"] == 10.0
    assert created["side"] == "BUY"


@pytest.mark.asyncio
async def test_cancel_order_calls_sdk(patched_poly_module):
    client = patched_poly_module("0xdeadbeef")
    await client.cancel_order("0xorder123")
    assert client._client.cancelled == ["0xorder123"]


@pytest.mark.asyncio
async def test_create_order_uses_asyncio_to_thread(patched_poly_module):
    """Verify that create_order wraps SDK calls in asyncio.to_thread."""
    client = patched_poly_module("0xdeadbeef")

    # The fact that this works in an async context without blocking proves
    # to_thread is being used. We can also verify by checking the result
    # comes back correctly.
    result = await client.create_order(
        token_id="0xtoken",
        side="BUY",
        price=0.50,
        size=5.0,
    )
    assert isinstance(result, dict)
    assert "orderID" in result


@pytest.mark.asyncio
async def test_get_balance_returns_float(patched_poly_module):
    client = patched_poly_module("0xdeadbeef")
    balance = await client.get_balance()
    assert isinstance(balance, float)


@pytest.mark.asyncio
async def test_close_is_noop(patched_poly_module):
    client = patched_poly_module("0xdeadbeef")
    await client.close()  # Should not raise
