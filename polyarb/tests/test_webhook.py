"""Tests for Telegram webhook handler in polyarb.daemon.server."""

from starlette.testclient import TestClient

from polyarb.config import Config
from polyarb.daemon.server import create_app
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
    async def answer_callback(self, callback_query_id):
        pass


# ── Helpers ────────────────────────────────────────────────


def _make_client(approval_manager=None, telegram_bot=None) -> TestClient:
    state = State(config=Config())
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

    resp = client.post("/telegram/webhook", json={
        "callback_query": {
            "id": "cb_1",
            "data": "approve:appr_123",
            "from": {"id": 999},
        }
    })

    assert resp.status_code == 200
    assert "appr_123" in mgr.approved


def test_webhook_reject():
    mgr = FakeApprovalManager()
    bot = FakeBot()
    client = _make_client(approval_manager=mgr, telegram_bot=bot)

    resp = client.post("/telegram/webhook", json={
        "callback_query": {
            "id": "cb_2",
            "data": "reject:appr_456",
            "from": {"id": 999},
        }
    })

    assert resp.status_code == 200
    assert "appr_456" in mgr.rejected


def test_webhook_no_callback_query():
    mgr = FakeApprovalManager()
    bot = FakeBot()
    client = _make_client(approval_manager=mgr, telegram_bot=bot)

    resp = client.post("/telegram/webhook", json={
        "message": {"text": "hello"}
    })

    assert resp.status_code == 200
    assert mgr.approved == []
    assert mgr.rejected == []


def test_webhook_invalid_callback_data():
    mgr = FakeApprovalManager()
    bot = FakeBot()
    client = _make_client(approval_manager=mgr, telegram_bot=bot)

    resp = client.post("/telegram/webhook", json={
        "callback_query": {
            "id": "cb_3",
            "data": "garbage",
            "from": {"id": 999},
        }
    })

    assert resp.status_code == 200
    assert mgr.approved == []
    assert mgr.rejected == []


def test_webhook_not_configured():
    client = _make_client()  # no approval_manager, no telegram_bot

    resp = client.post("/telegram/webhook", json={
        "callback_query": {
            "id": "cb_4",
            "data": "approve:appr_789",
            "from": {"id": 999},
        }
    })

    assert resp.status_code == 200
