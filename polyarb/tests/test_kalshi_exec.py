"""Tests for Kalshi execution module — auth signing, order mapping, executor."""

import base64
import tempfile

import pytest

pytest.importorskip("cryptography", reason="cryptography required for kalshi exec tests")
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from polyarb.execution.kalshi import KalshiAuth, KalshiExecutor  # noqa: E402
from polyarb.models import (  # noqa: E402
    Action,
    ArbType,
    Market,
    Opportunity,
    Order,
    OrderSet,
    Side,
    Token,
)

# ── Helpers ─────────────────────────────────────────────────


def _gen_rsa_pem() -> tuple[str, object]:
    """Generate a temporary RSA key pair, return (pem_path, public_key)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_bytes = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as tmp:
        tmp.write(pem_bytes)
    return tmp.name, private_key.public_key()


def _kalshi_order(
    ticker: str = "TEST-MKT",
    side: Side = Side.YES,
    action: Action = Action.BUY,
    price: float = 0.42,
    size: float = 10.0,
) -> Order:
    return Order(
        token_id=f"{ticker}:{side.value.lower()}",
        side=side,
        action=action,
        price=price,
        size=size,
    )


def _kalshi_market(ticker: str = "TEST-MKT", yes_mid: float = 0.50) -> Market:
    return Market(
        condition_id=ticker,
        question="Test",
        yes_token=Token(f"{ticker}:yes", Side.YES, yes_mid, yes_mid - 0.01, yes_mid + 0.01),
        no_token=Token(f"{ticker}:no", Side.NO, round(1 - yes_mid, 4), 0.0, 0.0),
        platform="kalshi",
    )


# ── KalshiAuth tests ───────────────────────────────────────


def test_auth_loads_key_and_signs():
    """Auth creates valid RSA-PSS signatures."""
    pem_path, pub_key = _gen_rsa_pem()
    auth = KalshiAuth("test-key-id", pem_path)
    hdrs = auth.headers("GET", "/trade-api/v2/portfolio/balance")

    assert hdrs["KALSHI-ACCESS-KEY"] == "test-key-id"
    assert hdrs["KALSHI-ACCESS-TIMESTAMP"].isdigit()
    assert len(hdrs["KALSHI-ACCESS-SIGNATURE"]) > 0

    # Verify signature with the public key
    timestamp = hdrs["KALSHI-ACCESS-TIMESTAMP"]
    message = f"{timestamp}GET/trade-api/v2/portfolio/balance".encode()
    sig_bytes = base64.b64decode(hdrs["KALSHI-ACCESS-SIGNATURE"])

    # Should not raise
    pub_key.verify(  # type: ignore[attr-defined]
        sig_bytes,
        message,
        asym_padding.PSS(
            mgf=asym_padding.MGF1(hashes.SHA256()),
            salt_length=asym_padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )


def test_auth_strips_query_params():
    """Signature message must not include query parameters."""
    pem_path, pub_key = _gen_rsa_pem()
    auth = KalshiAuth("k", pem_path)

    h1 = auth.headers("GET", "/trade-api/v2/portfolio/positions?limit=100")
    h2 = auth.headers("GET", "/trade-api/v2/portfolio/positions")

    # Both should sign the same path (ignoring query)
    # We can't directly compare signatures (different timestamps), but
    # we verify both produce valid signatures over the stripped path
    for hdrs in [h1, h2]:
        ts = hdrs["KALSHI-ACCESS-TIMESTAMP"]
        msg = f"{ts}GET/trade-api/v2/portfolio/positions".encode()
        sig = base64.b64decode(hdrs["KALSHI-ACCESS-SIGNATURE"])
        pub_key.verify(  # type: ignore[attr-defined]
            sig,
            msg,
            asym_padding.PSS(
                mgf=asym_padding.MGF1(hashes.SHA256()),
                salt_length=asym_padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )


def test_auth_bad_key_path():
    with pytest.raises(FileNotFoundError):
        KalshiAuth("key", "/nonexistent/path.pem")


# ── Order mapping tests ─────────────────────────────────────


def test_ticker_extracted_from_token_id():
    """Token ID 'TICKER:yes' → ticker='TICKER'."""
    order = _kalshi_order(ticker="KXBTC-25DEC-T100")
    assert order.token_id.rsplit(":", 1)[0] == "KXBTC-25DEC-T100"


def test_price_to_cents_conversion():
    """0.42 → 42 cents, 0.05 → 5 cents, 0.99 → 99 cents."""
    for price, expected in [(0.42, 42), (0.05, 5), (0.99, 99), (0.01, 1)]:
        cents = max(1, min(99, round(price * 100)))
        assert cents == expected


def test_price_clamped_to_valid_range():
    """Prices are clamped to 1-99 cents."""
    assert max(1, min(99, round(0.001 * 100))) == 1  # floor at 1
    assert max(1, min(99, round(0.999 * 100))) == 99  # ceil at 99
    assert max(1, min(99, round(0.0 * 100))) == 1  # zero → 1


def test_side_and_action_mapping():
    """Our enums map correctly to Kalshi's lowercase strings."""
    assert Side.YES.value.lower() == "yes"
    assert Side.NO.value.lower() == "no"
    assert Action.BUY.value.lower() == "buy"
    assert Action.SELL.value.lower() == "sell"


# ── KalshiExecutor tests (no network) ──────────────────────


class FakeClient:
    """Minimal KalshiClient stand-in for testing executor logic."""

    def __init__(self, balance: float = 1000.0, demo: bool = True):
        self.demo = demo
        self._balance = balance
        self.orders_placed: list[dict] = []
        self.cancelled_ids: list[str] = []

    def get_balance(self) -> float:
        return self._balance

    def create_order(self, **kwargs) -> dict:
        self.orders_placed.append(kwargs)
        return {
            "order_id": f"fake-{len(self.orders_placed)}",
            "status": "executed",
            "fill_count_fp": str(kwargs.get("count", 0)),
            "remaining_count_fp": "0",
        }

    def cancel_order(self, order_id: str) -> dict:
        self.cancelled_ids.append(order_id)
        return {"order": {"status": "canceled"}}


def _make_order_set() -> OrderSet:
    m = _kalshi_market("TEST-MKT", 0.40)
    opp = Opportunity(
        ArbType.SINGLE_UNDERPRICE,
        (m,),
        expected_profit_per_share=0.06,
    )
    return OrderSet(
        opportunity=opp,
        orders=[
            _kalshi_order("TEST-MKT", Side.YES, Action.BUY, 0.41, 10),
            _kalshi_order("TEST-MKT", Side.NO, Action.BUY, 0.53, 10),
        ],
        total_cost=9.40,
        expected_payout=10.0,
    )


def test_executor_places_all_orders():
    client = FakeClient(balance=100.0)
    executor = KalshiExecutor(client=client)  # type: ignore[arg-type]
    os = _make_order_set()

    result = executor.execute(os)

    assert result is True
    assert len(client.orders_placed) == 2
    assert client.orders_placed[0]["ticker"] == "TEST-MKT"
    assert client.orders_placed[0]["side"] == "yes"
    assert client.orders_placed[0]["action"] == "buy"
    assert client.orders_placed[0]["price_cents"] == 41
    assert client.orders_placed[1]["side"] == "no"
    assert client.orders_placed[1]["price_cents"] == 53


def test_executor_rejects_insufficient_balance():
    client = FakeClient(balance=1.0)  # Too low
    executor = KalshiExecutor(client=client)  # type: ignore[arg-type]
    os = _make_order_set()  # costs $9.40

    result = executor.execute(os)

    assert result is False
    assert len(client.orders_placed) == 0


def test_executor_tracks_trades_and_profit():
    client = FakeClient(balance=100.0)
    executor = KalshiExecutor(client=client)  # type: ignore[arg-type]
    os = _make_order_set()

    executor.execute(os)

    assert len(executor.trades) == 1
    assert executor.total_profit == os.expected_profit


def test_executor_cancels_on_partial_failure():
    """If leg 2 fails, leg 1's order should be cancelled."""

    class FailSecondLeg(FakeClient):
        def create_order(self, **kwargs):
            if len(self.orders_placed) == 1:
                raise RuntimeError("API error")
            return super().create_order(**kwargs)

    client = FailSecondLeg(balance=100.0)
    executor = KalshiExecutor(client=client)  # type: ignore[arg-type]
    os = _make_order_set()

    result = executor.execute(os)

    assert result is False
    assert len(executor.trades) == 0
    # First order was placed, then second failed, so first should be cancelled
    assert len(client.orders_placed) == 1
    assert client.cancelled_ids == ["fake-1"]
