"""Tests for ApprovalManager pending tracking, alerting, and approval flow."""

from __future__ import annotations

import pytest

from polyarb.config import Config
from polyarb.daemon.state import State
from polyarb.matching.matcher import MatchedPair
from polyarb.models import Market, Side, Token
from polyarb.notifications.approval import ApprovalManager

# ── Fake dependencies ──────��───────────────────────────────


class FakeBot:
    def __init__(self):
        self.alerts: list[tuple[str, MatchedPair]] = []
        self.edits: list[tuple[int, str]] = []
        self.expired: list[int] = []
        self.rejected: list[int] = []
        self._next_msg_id = 100

    async def send_alert(self, approval_id: str, match: MatchedPair) -> int:
        self.alerts.append((approval_id, match))
        self._next_msg_id += 1
        return self._next_msg_id - 1

    async def edit_result(self, message_id: int, text: str) -> None:
        self.edits.append((message_id, text))

    async def edit_expired(self, message_id: int) -> None:
        self.expired.append(message_id)

    async def edit_rejected(self, message_id: int) -> None:
        self.rejected.append(message_id)


class FakeKalshiClient:
    def __init__(self, result=None):
        self.orders: list[dict] = []
        self._result = result or {"order_id": "ord_1", "status": "resting"}

    async def create_order(self, **kwargs):
        self.orders.append(kwargs)
        return self._result


# ── Helpers ────────────────────────────────────────────────


def _make_market(
    cid: str,
    question: str,
    platform: str,
    yes_ask: float,
    no_ask: float | None = None,
) -> Market:
    if no_ask is None:
        no_ask = round(1.0 - yes_ask, 4)
    return Market(
        condition_id=cid,
        question=question,
        yes_token=Token("y-" + cid, Side.YES, yes_ask, yes_ask - 0.01, yes_ask),
        no_token=Token("n-" + cid, Side.NO, no_ask, no_ask - 0.01, no_ask),
        platform=platform,
    )


def _profitable_pair() -> MatchedPair:
    """Profitable after fees.

    Direction: buy YES on Kalshi (0.40) + buy NO on Poly (0.50)
    Gross cost = 0.90, well under 1.00 → profitable even after fees.
    """
    poly = _make_market("poly-1", "Will BTC hit $100k?", "polymarket", 0.50, no_ask=0.50)
    kalshi = _make_market("kalshi-1", "Bitcoin above $100k?", "kalshi", 0.40, no_ask=0.60)
    return MatchedPair(poly_market=poly, kalshi_market=kalshi, confidence=0.85)


def _unprofitable_pair() -> MatchedPair:
    """Both directions unprofitable after fees.

    Symmetric prices — no arb exists.
    """
    poly = _make_market("poly-1", "Will BTC hit $100k?", "polymarket", 0.55, no_ask=0.55)
    kalshi = _make_market("kalshi-1", "Bitcoin above $100k?", "kalshi", 0.55, no_ask=0.55)
    return MatchedPair(poly_market=poly, kalshi_market=kalshi, confidence=0.85)


def _make_manager(
    config: Config | None = None,
    state: State | None = None,
    bot: FakeBot | None = None,
    kalshi: FakeKalshiClient | None = None,
) -> tuple[ApprovalManager, FakeBot, FakeKalshiClient, State]:
    cfg = config or Config()
    st = state or State(config=cfg)
    b = bot or FakeBot()
    k = kalshi or FakeKalshiClient()
    mgr = ApprovalManager(state=st, bot=b, kalshi_client=k, config=cfg)
    return mgr, b, k, st


# ── Tests: should_alert ────────────────────────────────────


def test_should_alert_first_time():
    mgr, _, _, _ = _make_manager()
    match = _profitable_pair()
    assert mgr.should_alert(match) is True


def test_should_alert_unprofitable_returns_false():
    mgr, _, _, _ = _make_manager()
    match = _unprofitable_pair()
    assert mgr.should_alert(match) is False


def test_should_alert_same_profit_returns_false():
    mgr, _, _, _ = _make_manager()
    match = _profitable_pair()
    key = f"{match.poly_market.condition_id}:{match.kalshi_market.condition_id}"
    mgr._alerted[key] = mgr.fee_adjusted_profit(match)
    assert mgr.should_alert(match) is False


def test_should_alert_higher_profit_returns_true():
    mgr, _, _, _ = _make_manager()
    match = _profitable_pair()
    key = f"{match.poly_market.condition_id}:{match.kalshi_market.condition_id}"
    mgr._alerted[key] = mgr.fee_adjusted_profit(match) - 0.01  # lower than current
    assert mgr.should_alert(match) is True


def test_should_alert_lower_profit_returns_false():
    mgr, _, _, _ = _make_manager()
    match = _profitable_pair()
    key = f"{match.poly_market.condition_id}:{match.kalshi_market.condition_id}"
    mgr._alerted[key] = mgr.fee_adjusted_profit(match) + 0.01  # higher than current
    assert mgr.should_alert(match) is False


# ── Tests: fee_adjusted_profit ────────────────────────────


def test_fee_adjusted_profit_positive():
    mgr, _, _, _ = _make_manager()
    match = _profitable_pair()
    profit = mgr.fee_adjusted_profit(match)
    assert profit > 0


def test_fee_adjusted_profit_negative_for_unprofitable():
    mgr, _, _, _ = _make_manager()
    match = _unprofitable_pair()
    profit = mgr.fee_adjusted_profit(match)
    assert profit <= 0


# ── Tests: on_new_matches ──────────────────────────────────


@pytest.mark.asyncio
async def test_on_new_matches_sends_alert_for_profitable():
    mgr, bot, _, _ = _make_manager()
    match = _profitable_pair()

    await mgr.on_new_matches([match])

    assert len(bot.alerts) == 1
    assert len(mgr._pending) == 1
    approval_id = list(mgr._pending.keys())[0]
    pending = mgr._pending[approval_id]
    assert pending.match_key == "poly-1:kalshi-1"


@pytest.mark.asyncio
async def test_on_new_matches_skips_negative_profit():
    mgr, bot, _, _ = _make_manager()
    match = _unprofitable_pair()

    await mgr.on_new_matches([match])

    assert len(bot.alerts) == 0
    assert len(mgr._pending) == 0


@pytest.mark.asyncio
async def test_on_new_matches_respects_should_alert():
    mgr, bot, _, _ = _make_manager()
    match = _profitable_pair()

    await mgr.on_new_matches([match])
    await mgr.on_new_matches([match])  # same match, same profit

    assert len(bot.alerts) == 1  # only one alert sent


# ── Tests: handle_approve (execution blocked) ────────────


@pytest.mark.asyncio
async def test_handle_approve_returns_execution_blocked():
    mgr, bot, kalshi, state = _make_manager()
    match = _profitable_pair()
    state.matches = [match]

    await mgr.on_new_matches([match])
    approval_id = list(mgr._pending.keys())[0]

    result = await mgr.handle_approve(approval_id)

    # No orders placed — execution is blocked
    assert len(kalshi.orders) == 0
    assert "disabled" in result.lower() or "not yet implemented" in result.lower()
    assert len(bot.edits) == 1


@pytest.mark.asyncio
async def test_handle_approve_unknown_id():
    mgr, _, _, _ = _make_manager()
    result = await mgr.handle_approve("nonexistent-id")
    assert "not found" in result.lower() or "expired" in result.lower()


# ���─ Tests: handle_reject ───────────────────────────────────


@pytest.mark.asyncio
async def test_handle_reject():
    mgr, bot, _, _ = _make_manager()
    match = _profitable_pair()

    await mgr.on_new_matches([match])
    approval_id = list(mgr._pending.keys())[0]

    await mgr.handle_reject(approval_id)

    assert approval_id not in mgr._pending
    assert len(bot.rejected) == 1


# ── Tests: expire_stale ───────────────────────────────────


@pytest.mark.asyncio
async def test_expire_stale_removes_old_approvals():
    cfg = Config(approval_timeout=0)  # immediate expiry
    mgr, bot, _, _ = _make_manager(config=cfg)
    match = _profitable_pair()

    await mgr.on_new_matches([match])
    assert len(mgr._pending) == 1

    await mgr.expire_stale()

    assert len(mgr._pending) == 0
    assert len(bot.expired) == 1


# ── Tests: Kelly fields in PendingApproval ────────────────


@pytest.mark.asyncio
async def test_pending_approval_kelly_fields_when_configured():
    """Kelly fields should be populated when bankroll > 0."""
    cfg = Config(bankroll=1000.0, kelly_fraction=0.5)
    mgr, bot, _, _ = _make_manager(config=cfg)
    match = _profitable_pair()

    await mgr.on_new_matches([match])

    pending = list(mgr._pending.values())[0]
    assert pending.kelly_fraction_raw > 0.0
    assert pending.kelly_size > 0.0


@pytest.mark.asyncio
async def test_pending_approval_kelly_fields_when_disabled():
    """When Kelly is disabled, kelly_fraction_raw=0.0 and kelly_size=order_size."""
    cfg = Config(bankroll=0.0, order_size=15.0)
    mgr, bot, _, _ = _make_manager(config=cfg)
    match = _profitable_pair()

    await mgr.on_new_matches([match])

    pending = list(mgr._pending.values())[0]
    assert pending.kelly_fraction_raw == 0.0
    assert pending.kelly_size == 15.0


@pytest.mark.asyncio
async def test_expire_stale_keeps_fresh_approvals():
    cfg = Config(approval_timeout=9999)
    mgr, bot, _, _ = _make_manager(config=cfg)
    match = _profitable_pair()

    await mgr.on_new_matches([match])
    assert len(mgr._pending) == 1

    await mgr.expire_stale()

    assert len(mgr._pending) == 1
    assert len(bot.expired) == 0
