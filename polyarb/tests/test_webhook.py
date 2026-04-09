"""Tests for Telegram webhook handler in polyarb API."""

from starlette.testclient import TestClient

from polyarb.api.app import create_app
from polyarb.config import Config
from polyarb.daemon.state import State

# ── Fakes ──────────────────────────────────────────────────


class FakeApprovalManager:
    def __init__(self):
        self.approved = []
        self.rejected = []
        self._approve_result = "Executed"

    async def handle_approve(self, approval_id):
        self.approved.append(approval_id)
        return self._approve_result

    async def handle_reject(self, approval_id):
        self.rejected.append(approval_id)


class FakeBot:
    def __init__(self):
        self.digests: list[list] = []

    async def answer_callback(self, callback_query_id):
        pass

    async def send_digest(self, opps, limit=20):
        self.digests.append(opps)
        return 42


# ── Helpers ────────────────────────────────────────────────


def _make_client(approval_manager=None, telegram_bot=None, state=None) -> TestClient:
    state = state or State(config=Config())
    app = create_app(
        state,
        approval_manager=approval_manager,
        telegram_bot=telegram_bot,
    )
    return TestClient(app)


# ── Tests ──────────────────────────────────────────────────


def test_webhook_approve():
    mgr = FakeApprovalManager()
    bot = FakeBot()
    client = _make_client(approval_manager=mgr, telegram_bot=bot)

    resp = client.post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "cb_1",
                "data": "approve:appr_123",
                "from": {"id": 999},
            }
        },
    )

    assert resp.status_code == 200
    assert "appr_123" in mgr.approved


def test_webhook_reject():
    mgr = FakeApprovalManager()
    bot = FakeBot()
    client = _make_client(approval_manager=mgr, telegram_bot=bot)

    resp = client.post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "cb_2",
                "data": "reject:appr_456",
                "from": {"id": 999},
            }
        },
    )

    assert resp.status_code == 200
    assert "appr_456" in mgr.rejected


def test_webhook_no_callback_query():
    mgr = FakeApprovalManager()
    bot = FakeBot()
    client = _make_client(approval_manager=mgr, telegram_bot=bot)

    resp = client.post("/telegram/webhook", json={"message": {"text": "hello"}})

    assert resp.status_code == 200
    assert mgr.approved == []
    assert mgr.rejected == []


def test_webhook_invalid_callback_data():
    mgr = FakeApprovalManager()
    bot = FakeBot()
    client = _make_client(approval_manager=mgr, telegram_bot=bot)

    resp = client.post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "cb_3",
                "data": "garbage",
                "from": {"id": 999},
            }
        },
    )

    assert resp.status_code == 200
    assert mgr.approved == []
    assert mgr.rejected == []


def test_webhook_not_configured():
    client = _make_client()  # no approval_manager, no telegram_bot

    resp = client.post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "cb_4",
                "data": "approve:appr_789",
                "from": {"id": 999},
            }
        },
    )

    assert resp.status_code == 200


def test_webhook_scan_command():
    from polyarb.models import ArbType, Market, Opportunity, Side, Token

    def _mkt(cid):
        return Market(
            condition_id=cid,
            question=f"Will {cid} happen?",
            yes_token=Token(
                token_id=f"{cid}:y", side=Side.YES, midpoint=0.6, best_bid=0.59, best_ask=0.61
            ),
            no_token=Token(
                token_id=f"{cid}:n", side=Side.NO, midpoint=0.4, best_bid=0.39, best_ask=0.41
            ),
        )

    state = State(config=Config())
    state.opportunities = [
        Opportunity(
            arb_type=ArbType.SINGLE_UNDERPRICE, markets=(_mkt("a"),), expected_profit_per_share=0.03
        ),
    ]

    bot = FakeBot()
    client = _make_client(telegram_bot=bot, state=state)

    resp = client.post("/telegram/webhook", json={"message": {"text": "/scan"}})

    assert resp.status_code == 200
    assert len(bot.digests) == 1
    assert len(bot.digests[0]) == 1


def test_webhook_scan_command_no_bot():
    client = _make_client()  # no bot configured

    resp = client.post("/telegram/webhook", json={"message": {"text": "/scan"}})

    assert resp.status_code == 200
