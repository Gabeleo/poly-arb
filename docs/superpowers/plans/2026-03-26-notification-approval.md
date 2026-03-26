# Notification + Approval Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Telegram push notifications with inline approve/reject buttons and a timed approval flow for arb execution.

**Architecture:** TelegramBot sends alerts via Bot API (httpx async), ApprovalManager tracks pending approvals with timeout/re-alert logic, Starlette webhook route receives button callbacks. Optional — daemon works without Telegram when env vars are not set.

**Tech Stack:** Telegram Bot API (plain HTTPS via httpx), no new dependencies

**Spec:** `docs/superpowers/specs/2026-03-26-notification-approval-design.md`

---

## File Structure

### New files

| File | Responsibility |
|------|---------------|
| `polyarb/notifications/__init__.py` | Package marker |
| `polyarb/notifications/telegram.py` | Telegram Bot API client (send alerts, edit messages) |
| `polyarb/notifications/approval.py` | Approval manager (pending tracking, approve/reject/expire, re-alert logic) |
| `polyarb/tests/test_telegram.py` | Tests for TelegramBot with mocked httpx |
| `polyarb/tests/test_approval.py` | Tests for ApprovalManager logic |
| `polyarb/tests/test_webhook.py` | Tests for the Telegram webhook route |

### Modified files

| File | Change |
|------|--------|
| `polyarb/config.py` | Add `approval_timeout: float = 120.0` |
| `polyarb/daemon/engine.py` | Hook approval manager into scan loop |
| `polyarb/daemon/server.py` | Add `/telegram/webhook` route, accept `approval_manager` param |
| `polyarb/daemon/__main__.py` | Wire up TelegramBot + ApprovalManager from env vars |

---

## Task 1: Config + TelegramBot

**Files:**
- Modify: `polyarb/config.py`
- Create: `polyarb/notifications/__init__.py`
- Create: `polyarb/notifications/telegram.py`
- Create: `polyarb/tests/test_telegram.py`

### Step 1: Add approval_timeout to Config

- [ ] **Update config.py**

Add `approval_timeout` field to the Config dataclass in `polyarb/config.py`:

```python
@dataclass
class Config:
    min_profit: float = 0.005
    max_prob: float = 0.95
    scan_interval: float = 10.0
    order_size: float = 10.0
    dedup_window: int = 60
    approval_timeout: float = 120.0
```

- [ ] **Run existing tests**

Run: `pytest -v`
Expected: All 102 tests pass (new field has a default, nothing breaks).

### Step 2: Write TelegramBot tests

- [ ] **Create package marker**

Create `polyarb/notifications/__init__.py` as an empty file.

- [ ] **Write tests**

Create `polyarb/tests/test_telegram.py`:

```python
from __future__ import annotations

import json

import httpx
import pytest

from polyarb.notifications.telegram import TelegramBot
from polyarb.matching.matcher import MatchedPair
from polyarb.models import Market, Side, Token


def _make_market(cid: str, question: str, platform: str, yes_ask: float = 0.65) -> Market:
    return Market(
        condition_id=cid,
        question=question,
        yes_token=Token(
            token_id=f"{cid}:y", side=Side.YES,
            midpoint=yes_ask - 0.02, best_bid=yes_ask - 0.04, best_ask=yes_ask,
        ),
        no_token=Token(
            token_id=f"{cid}:n", side=Side.NO,
            midpoint=round(1 - yes_ask + 0.02, 4),
            best_bid=round(1 - yes_ask, 4),
            best_ask=round(1 - yes_ask + 0.04, 4),
        ),
        platform=platform,
        event_slug=f"evt-{cid}",
    )


def _make_pair(profit_offset: float = 0.03) -> MatchedPair:
    pm = _make_market("p1", "Will BTC hit 100k?", "polymarket", 0.65)
    km = _make_market("k1", "Bitcoin above 100k?", "kalshi", 0.62)
    return MatchedPair(poly_market=pm, kalshi_market=km, confidence=0.85)


class _Recorder:
    """Captures requests sent to the Telegram API."""

    def __init__(self):
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if "sendMessage" in path:
            return httpx.Response(200, json={
                "ok": True,
                "result": {"message_id": 42},
            })
        if "editMessageText" in path:
            return httpx.Response(200, json={"ok": True, "result": {}})
        if "answerCallbackQuery" in path:
            return httpx.Response(200, json={"ok": True, "result": True})
        if "setWebhook" in path:
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(404, json={"ok": False})


async def test_send_alert_returns_message_id():
    rec = _Recorder()
    client = httpx.AsyncClient(transport=httpx.MockTransport(rec.handler))
    bot = TelegramBot(token="tok123", chat_id="999", client=client)

    msg_id = await bot.send_alert("appr_1", _make_pair())

    assert msg_id == 42
    assert len(rec.requests) == 1
    body = json.loads(rec.requests[0].content)
    assert body["chat_id"] == "999"
    assert "BTC" in body["text"] or "100k" in body["text"]
    assert "reply_markup" in body
    markup = body["reply_markup"]
    buttons = markup["inline_keyboard"][0]
    assert any("approve:appr_1" in b["callback_data"] for b in buttons)
    assert any("reject:appr_1" in b["callback_data"] for b in buttons)
    await bot.close()


async def test_send_alert_message_contains_profit():
    rec = _Recorder()
    client = httpx.AsyncClient(transport=httpx.MockTransport(rec.handler))
    bot = TelegramBot(token="tok123", chat_id="999", client=client)

    await bot.send_alert("appr_1", _make_pair())

    body = json.loads(rec.requests[0].content)
    text = body["text"]
    assert "Profit" in text or "profit" in text
    await bot.close()


async def test_edit_result():
    rec = _Recorder()
    client = httpx.AsyncClient(transport=httpx.MockTransport(rec.handler))
    bot = TelegramBot(token="tok123", chat_id="999", client=client)

    await bot.edit_result(42, "Executed — filled 10 contracts")

    assert len(rec.requests) == 1
    body = json.loads(rec.requests[0].content)
    assert body["message_id"] == 42
    assert body["chat_id"] == "999"
    assert "Executed" in body["text"]
    await bot.close()


async def test_edit_expired():
    rec = _Recorder()
    client = httpx.AsyncClient(transport=httpx.MockTransport(rec.handler))
    bot = TelegramBot(token="tok123", chat_id="999", client=client)

    await bot.edit_expired(42)

    body = json.loads(rec.requests[0].content)
    assert "Expired" in body["text"] or "expired" in body["text"]
    await bot.close()


async def test_edit_rejected():
    rec = _Recorder()
    client = httpx.AsyncClient(transport=httpx.MockTransport(rec.handler))
    bot = TelegramBot(token="tok123", chat_id="999", client=client)

    await bot.edit_rejected(42)

    body = json.loads(rec.requests[0].content)
    assert "Rejected" in body["text"] or "rejected" in body["text"]
    await bot.close()


async def test_answer_callback():
    rec = _Recorder()
    client = httpx.AsyncClient(transport=httpx.MockTransport(rec.handler))
    bot = TelegramBot(token="tok123", chat_id="999", client=client)

    await bot.answer_callback("callback_123")

    body = json.loads(rec.requests[0].content)
    assert body["callback_query_id"] == "callback_123"
    await bot.close()


async def test_set_webhook():
    rec = _Recorder()
    client = httpx.AsyncClient(transport=httpx.MockTransport(rec.handler))
    bot = TelegramBot(token="tok123", chat_id="999", client=client)

    await bot.set_webhook("https://example.com/telegram/webhook")

    body = json.loads(rec.requests[0].content)
    assert body["url"] == "https://example.com/telegram/webhook"
    await bot.close()
```

- [ ] **Run tests to verify they fail**

Run: `pytest polyarb/tests/test_telegram.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'polyarb.notifications.telegram'`

### Step 3: Implement TelegramBot

- [ ] **Create telegram.py**

Create `polyarb/notifications/telegram.py`:

```python
"""Telegram Bot API client for arb alerts with inline approve/reject buttons."""

from __future__ import annotations

import json

import httpx

from polyarb.matching.matcher import MatchedPair

TELEGRAM_API = "https://api.telegram.org"


def _format_alert(match: MatchedPair) -> str:
    pm = match.poly_market
    km = match.kalshi_market
    profit, kalshi_side, kalshi_desc, poly_desc, kalshi_price = match.best_arb

    return (
        "\U0001f514 New Cross-Platform Arb\n"
        "\n"
        f"Polymarket: {pm.question}\n"
        f"  YES ask: ${pm.yes_token.best_ask:.4f}\n"
        "\n"
        f"Kalshi: {km.question}\n"
        f"  YES ask: ${km.yes_token.best_ask:.4f}\n"
        "\n"
        f"Action: {kalshi_desc} + {poly_desc}\n"
        f"Profit/share: ${profit:.4f}"
    )


def _inline_keyboard(approval_id: str) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "Approve \u2713", "callback_data": f"approve:{approval_id}"},
            {"text": "Reject \u2717", "callback_data": f"reject:{approval_id}"},
        ]]
    }


class TelegramBot:
    """Async Telegram Bot API client using httpx."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None
        self._base = f"{TELEGRAM_API}/bot{token}"

    async def _post(self, method: str, data: dict) -> dict:
        resp = await self._client.post(
            f"{self._base}/{method}",
            content=json.dumps(data).encode(),
            headers={"Content-Type": "application/json"},
        )
        return resp.json()

    async def send_alert(self, approval_id: str, match: MatchedPair) -> int:
        """Send arb alert with Approve/Reject buttons. Returns message_id."""
        result = await self._post("sendMessage", {
            "chat_id": self._chat_id,
            "text": _format_alert(match),
            "reply_markup": _inline_keyboard(approval_id),
        })
        return result.get("result", {}).get("message_id", 0)

    async def edit_result(self, message_id: int, text: str) -> None:
        """Edit message to show execution result."""
        await self._post("editMessageText", {
            "chat_id": self._chat_id,
            "message_id": message_id,
            "text": f"\u2705 {text}",
        })

    async def edit_expired(self, message_id: int) -> None:
        """Edit message to show expiry."""
        await self._post("editMessageText", {
            "chat_id": self._chat_id,
            "message_id": message_id,
            "text": "\u23f0 Expired (no response)",
        })

    async def edit_rejected(self, message_id: int) -> None:
        """Edit message to show rejection."""
        await self._post("editMessageText", {
            "chat_id": self._chat_id,
            "message_id": message_id,
            "text": "\u274c Rejected",
        })

    async def answer_callback(self, callback_query_id: str) -> None:
        """Acknowledge a button press (dismisses spinner in Telegram)."""
        await self._post("answerCallbackQuery", {
            "callback_query_id": callback_query_id,
        })

    async def set_webhook(self, url: str) -> None:
        """Register webhook URL with Telegram."""
        await self._post("setWebhook", {"url": url})

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
```

- [ ] **Run telegram tests**

Run: `pytest polyarb/tests/test_telegram.py -v`
Expected: All 7 tests PASS.

### Step 4: Run full suite and commit

- [ ] **Run all tests**

Run: `pytest -v`
Expected: All tests PASS (102 existing + 7 new = 109).

- [ ] **Commit**

```bash
git add polyarb/config.py polyarb/notifications/ polyarb/tests/test_telegram.py
git commit -m "Add TelegramBot client and approval_timeout config

- TelegramBot: async httpx client for Bot API (sendMessage with
  inline buttons, editMessageText, answerCallbackQuery, setWebhook)
- Add approval_timeout (default 120s) to Config
- 7 tests with mocked httpx transport"
```

---

## Task 2: ApprovalManager

**Files:**
- Create: `polyarb/notifications/approval.py`
- Create: `polyarb/tests/test_approval.py`

### Step 1: Write ApprovalManager tests

- [ ] **Write tests**

Create `polyarb/tests/test_approval.py`:

```python
from __future__ import annotations

import time

import pytest

from polyarb.config import Config
from polyarb.daemon.state import State
from polyarb.matching.matcher import MatchedPair
from polyarb.models import Market, Side, Token
from polyarb.notifications.approval import ApprovalManager, PendingApproval


def _make_market(cid: str, question: str, platform: str, yes_ask: float = 0.65) -> Market:
    return Market(
        condition_id=cid,
        question=question,
        yes_token=Token(
            token_id=f"{cid}:y", side=Side.YES,
            midpoint=yes_ask - 0.02, best_bid=yes_ask - 0.04, best_ask=yes_ask,
        ),
        no_token=Token(
            token_id=f"{cid}:n", side=Side.NO,
            midpoint=round(1 - yes_ask + 0.02, 4),
            best_bid=round(1 - yes_ask, 4),
            best_ask=round(1 - yes_ask + 0.04, 4),
        ),
        platform=platform,
        event_slug=f"evt-{cid}",
    )


def _make_pair(poly_ask: float = 0.35, kalshi_ask: float = 0.62) -> MatchedPair:
    pm = _make_market("p1", "BTC 100k?", "polymarket", 0.65)
    # Override NO ask for poly to control profit
    pm_no = Token(
        token_id="p1:n", side=Side.NO,
        midpoint=round(1 - 0.65 + 0.02, 4),
        best_bid=round(1 - 0.65, 4),
        best_ask=poly_ask,
    )
    pm = Market(
        condition_id=pm.condition_id, question=pm.question,
        yes_token=pm.yes_token, no_token=pm_no,
        platform="polymarket", event_slug="evt-p1",
    )
    km = _make_market("k1", "Bitcoin 100k?", "kalshi", kalshi_ask)
    return MatchedPair(poly_market=pm, kalshi_market=km, confidence=0.85)


class FakeBot:
    """Records calls instead of hitting Telegram."""

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
    """Records order calls."""

    def __init__(self, result: dict | None = None):
        self.orders: list[dict] = []
        self._result = result or {"order_id": "ord_1", "status": "resting"}

    async def create_order(self, **kwargs) -> dict:
        self.orders.append(kwargs)
        return self._result


def _make_manager(
    bot: FakeBot | None = None,
    kalshi_client: FakeKalshiClient | None = None,
    config: Config | None = None,
) -> tuple[ApprovalManager, State, FakeBot, FakeKalshiClient]:
    config = config or Config()
    state = State(config=config)
    bot = bot or FakeBot()
    kc = kalshi_client or FakeKalshiClient()
    mgr = ApprovalManager(state=state, bot=bot, kalshi_client=kc, config=config)
    return mgr, state, bot, kc


# ── should_alert tests ──────────────────────────────────────


def test_should_alert_first_time():
    mgr, _, _, _ = _make_manager()
    pair = _make_pair()
    assert mgr.should_alert(pair) is True


def test_should_alert_same_profit_returns_false():
    mgr, _, _, _ = _make_manager()
    pair = _make_pair()
    # Simulate having alerted this pair before
    key = f"{pair.poly_market.condition_id}:{pair.kalshi_market.condition_id}"
    mgr._alerted[key] = pair.best_arb[0]
    assert mgr.should_alert(pair) is False


def test_should_alert_higher_profit_returns_true():
    mgr, _, _, _ = _make_manager()
    pair = _make_pair()
    key = f"{pair.poly_market.condition_id}:{pair.kalshi_market.condition_id}"
    mgr._alerted[key] = pair.best_arb[0] - 0.01  # lower than current
    assert mgr.should_alert(pair) is True


def test_should_alert_lower_profit_returns_false():
    mgr, _, _, _ = _make_manager()
    pair = _make_pair()
    key = f"{pair.poly_market.condition_id}:{pair.kalshi_market.condition_id}"
    mgr._alerted[key] = pair.best_arb[0] + 0.01  # higher than current
    assert mgr.should_alert(pair) is False


# ── on_new_matches tests ────────────────────────────────────


async def test_on_new_matches_sends_alert_for_profitable():
    mgr, _, bot, _ = _make_manager()
    pair = _make_pair(poly_ask=0.35, kalshi_ask=0.62)
    # Ensure profit > 0: cost = 0.62 + 0.35 = 0.97, profit = 0.03
    assert pair.best_arb[0] > 0

    await mgr.on_new_matches([pair])

    assert len(bot.alerts) == 1
    assert len(mgr._pending) == 1


async def test_on_new_matches_skips_negative_profit():
    mgr, _, bot, _ = _make_manager()
    # Make an unprofitable pair: cost > 1.0
    pair = _make_pair(poly_ask=0.55, kalshi_ask=0.62)
    # cost = 0.62 + 0.55 = 1.17, profit = -0.17
    assert pair.best_arb[0] < 0

    await mgr.on_new_matches([pair])

    assert len(bot.alerts) == 0
    assert len(mgr._pending) == 0


async def test_on_new_matches_respects_should_alert():
    mgr, _, bot, _ = _make_manager()
    pair = _make_pair(poly_ask=0.35, kalshi_ask=0.62)

    await mgr.on_new_matches([pair])
    assert len(bot.alerts) == 1

    # Same pair again, same profit — should not re-alert
    await mgr.on_new_matches([pair])
    assert len(bot.alerts) == 1


# ── handle_approve tests ────────────────────────────────────


async def test_handle_approve_executes_trade():
    mgr, state, bot, kc = _make_manager()
    pair = _make_pair(poly_ask=0.35, kalshi_ask=0.62)
    state.matches = [pair]

    await mgr.on_new_matches([pair])
    approval_id = list(mgr._pending.keys())[0]

    result = await mgr.handle_approve(approval_id)

    assert len(kc.orders) == 1
    assert kc.orders[0]["ticker"] == "k1"
    assert approval_id not in mgr._pending
    assert len(bot.edits) == 1
    assert "Executed" in result or "executed" in result


async def test_handle_approve_rejects_if_no_longer_profitable():
    mgr, state, bot, kc = _make_manager()
    pair = _make_pair(poly_ask=0.35, kalshi_ask=0.62)
    state.matches = [pair]

    await mgr.on_new_matches([pair])
    approval_id = list(mgr._pending.keys())[0]

    # Now replace matches with an unprofitable version
    bad_pair = _make_pair(poly_ask=0.55, kalshi_ask=0.62)
    state.matches = [bad_pair]

    result = await mgr.handle_approve(approval_id)

    assert len(kc.orders) == 0
    assert "no longer profitable" in result.lower() or "not profitable" in result.lower()


async def test_handle_approve_unknown_id():
    mgr, _, _, _ = _make_manager()
    result = await mgr.handle_approve("nonexistent")
    assert "not found" in result.lower() or "unknown" in result.lower()


# ── handle_reject tests ─────────────────────────────────────


async def test_handle_reject():
    mgr, state, bot, _ = _make_manager()
    pair = _make_pair(poly_ask=0.35, kalshi_ask=0.62)
    state.matches = [pair]

    await mgr.on_new_matches([pair])
    approval_id = list(mgr._pending.keys())[0]

    await mgr.handle_reject(approval_id)

    assert approval_id not in mgr._pending
    assert len(bot.rejected) == 1


# ── expire_stale tests ──────────────────────────────────────


async def test_expire_stale_removes_old_approvals():
    config = Config(approval_timeout=0.0)  # immediate expiry
    mgr, state, bot, _ = _make_manager(config=config)
    pair = _make_pair(poly_ask=0.35, kalshi_ask=0.62)
    state.matches = [pair]

    await mgr.on_new_matches([pair])
    assert len(mgr._pending) == 1

    await mgr.expire_stale()

    assert len(mgr._pending) == 0
    assert len(bot.expired) == 1


async def test_expire_stale_keeps_fresh_approvals():
    config = Config(approval_timeout=9999.0)
    mgr, state, bot, _ = _make_manager(config=config)
    pair = _make_pair(poly_ask=0.35, kalshi_ask=0.62)
    state.matches = [pair]

    await mgr.on_new_matches([pair])

    await mgr.expire_stale()

    assert len(mgr._pending) == 1
    assert len(bot.expired) == 0
```

- [ ] **Run tests to verify they fail**

Run: `pytest polyarb/tests/test_approval.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'polyarb.notifications.approval'`

### Step 2: Implement ApprovalManager

- [ ] **Create approval.py**

Create `polyarb/notifications/approval.py`:

```python
"""Approval manager — tracks pending approvals, handles timeouts and re-alerts."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

from polyarb.config import Config
from polyarb.daemon.state import State
from polyarb.matching.matcher import MatchedPair

logger = logging.getLogger(__name__)


def _match_key(match: MatchedPair) -> str:
    return f"{match.poly_market.condition_id}:{match.kalshi_market.condition_id}"


@dataclass
class PendingApproval:
    approval_id: str
    match_key: str
    match_data: MatchedPair
    profit_at_alert: float
    telegram_message_id: int
    created_at: float  # time.monotonic()


class ApprovalManager:
    """Manages Telegram-based approval flow for arb execution."""

    def __init__(self, state: State, bot, kalshi_client, config: Config) -> None:
        self._state = state
        self._bot = bot
        self._kalshi_client = kalshi_client
        self._config = config
        self._pending: dict[str, PendingApproval] = {}
        self._alerted: dict[str, float] = {}  # match_key -> last_alerted_profit

    def should_alert(self, match: MatchedPair) -> bool:
        """True if match has never been alerted, or profit improved since last alert."""
        profit = match.best_arb[0]
        if profit <= 0:
            return False
        key = _match_key(match)
        last = self._alerted.get(key)
        if last is None:
            return True
        return profit > last

    async def on_new_matches(self, new_matches: list[MatchedPair]) -> None:
        """Called by engine after each scan. Sends alerts for qualifying matches."""
        for match in new_matches:
            if not self.should_alert(match):
                continue

            approval_id = uuid.uuid4().hex[:12]
            key = _match_key(match)
            profit = match.best_arb[0]

            msg_id = await self._bot.send_alert(approval_id, match)

            self._pending[approval_id] = PendingApproval(
                approval_id=approval_id,
                match_key=key,
                match_data=match,
                profit_at_alert=profit,
                telegram_message_id=msg_id,
                created_at=time.monotonic(),
            )
            self._alerted[key] = profit

            logger.info("Alert sent for %s (profit=$%.4f, id=%s)", key, profit, approval_id)

    async def handle_approve(self, approval_id: str) -> str:
        """Execute the trade. Returns result description."""
        pending = self._pending.pop(approval_id, None)
        if pending is None:
            return "Approval not found or already expired"

        # Find current match in state (may have updated prices)
        current_match = None
        for m in self._state.matches:
            if _match_key(m) == pending.match_key:
                current_match = m
                break

        if current_match is None:
            await self._bot.edit_result(
                pending.telegram_message_id, "Match no longer available"
            )
            return "Match no longer available"

        profit = current_match.best_arb[0]
        if profit <= 0:
            msg = "Arb no longer profitable, skipped"
            await self._bot.edit_result(pending.telegram_message_id, msg)
            return msg

        # Execute
        _, kalshi_side, kalshi_desc, poly_desc, kalshi_price = current_match.best_arb
        ticker = current_match.kalshi_market.condition_id
        price_cents = max(1, min(99, round(kalshi_price * 100)))
        count = max(1, int(self._config.order_size))

        try:
            result = await self._kalshi_client.create_order(
                ticker=ticker,
                side=kalshi_side,
                action="buy",
                price_cents=price_cents,
                count=count,
            )
            status = result.get("status", "unknown")
            filled = result.get("fill_count_fp", "0")
            msg = (
                f"Executed \u2014 {kalshi_desc} @ ${kalshi_price:.3f}\n"
                f"Status: {status}, filled: {filled}"
            )
            await self._bot.edit_result(pending.telegram_message_id, msg)
            logger.info("Approved %s: %s", approval_id, msg)
            return msg
        except Exception as e:
            msg = f"Execution failed: {e}"
            await self._bot.edit_result(pending.telegram_message_id, msg)
            logger.error("Execution failed for %s: %s", approval_id, e)
            return msg

    async def handle_reject(self, approval_id: str) -> None:
        """Cancel a pending approval."""
        pending = self._pending.pop(approval_id, None)
        if pending is not None:
            await self._bot.edit_rejected(pending.telegram_message_id)
            logger.info("Rejected %s", approval_id)

    async def expire_stale(self) -> None:
        """Expire approvals older than config.approval_timeout."""
        now = time.monotonic()
        expired_ids = [
            aid for aid, p in self._pending.items()
            if now - p.created_at >= self._config.approval_timeout
        ]
        for aid in expired_ids:
            pending = self._pending.pop(aid)
            await self._bot.edit_expired(pending.telegram_message_id)
            logger.info("Expired %s", aid)
```

- [ ] **Run approval tests**

Run: `pytest polyarb/tests/test_approval.py -v`
Expected: All 13 tests PASS.

### Step 3: Run full suite and commit

- [ ] **Run all tests**

Run: `pytest -v`
Expected: All tests PASS (102 + 7 + 13 = 122).

- [ ] **Commit**

```bash
git add polyarb/notifications/approval.py polyarb/tests/test_approval.py
git commit -m "Add ApprovalManager with pending tracking, timeout, and re-alert

- PendingApproval dataclass tracks match, profit, Telegram message ID
- should_alert: first-time or profit-improved re-alert logic
- handle_approve: re-checks profit, executes via Kalshi, edits Telegram msg
- handle_reject: cancels pending, edits Telegram msg
- expire_stale: removes timed-out approvals, edits Telegram msg
- 13 tests with fake bot and fake Kalshi client"
```

---

## Task 3: Daemon Integration

**Files:**
- Modify: `polyarb/daemon/engine.py`
- Modify: `polyarb/daemon/server.py`
- Modify: `polyarb/daemon/__main__.py`
- Create: `polyarb/tests/test_webhook.py`

### Step 1: Write webhook tests

- [ ] **Write tests**

Create `polyarb/tests/test_webhook.py`:

```python
from __future__ import annotations

from starlette.testclient import TestClient

from polyarb.config import Config
from polyarb.daemon.server import create_app
from polyarb.daemon.state import State
from polyarb.matching.matcher import MatchedPair
from polyarb.models import Market, Side, Token


def _make_market(cid: str, question: str, platform: str, yes_ask: float = 0.65) -> Market:
    return Market(
        condition_id=cid,
        question=question,
        yes_token=Token(
            token_id=f"{cid}:y", side=Side.YES,
            midpoint=yes_ask - 0.02, best_bid=yes_ask - 0.04, best_ask=yes_ask,
        ),
        no_token=Token(
            token_id=f"{cid}:n", side=Side.NO,
            midpoint=round(1 - yes_ask + 0.02, 4),
            best_bid=round(1 - yes_ask, 4),
            best_ask=round(1 - yes_ask + 0.04, 4),
        ),
        platform=platform,
        event_slug=f"evt-{cid}",
    )


def _make_pair() -> MatchedPair:
    return MatchedPair(
        poly_market=_make_market("p1", "BTC 100k?", "polymarket"),
        kalshi_market=_make_market("k1", "Bitcoin 100k?", "kalshi"),
        confidence=0.85,
    )


class FakeApprovalManager:
    def __init__(self):
        self.approved: list[str] = []
        self.rejected: list[str] = []
        self._approve_result = "Executed — filled 10"

    async def handle_approve(self, approval_id: str) -> str:
        self.approved.append(approval_id)
        return self._approve_result

    async def handle_reject(self, approval_id: str) -> None:
        self.rejected.append(approval_id)


class FakeBot:
    async def answer_callback(self, callback_query_id: str) -> None:
        pass


def _make_app(approval_manager=None, bot=None):
    state = State(config=Config())
    return create_app(
        state,
        approval_manager=approval_manager,
        telegram_bot=bot,
    )


def test_webhook_approve():
    mgr = FakeApprovalManager()
    bot = FakeBot()
    app = _make_app(approval_manager=mgr, bot=bot)
    client = TestClient(app)

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
    app = _make_app(approval_manager=mgr, bot=bot)
    client = TestClient(app)

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
    app = _make_app(approval_manager=mgr, bot=bot)
    client = TestClient(app)

    resp = client.post("/telegram/webhook", json={"message": {"text": "hello"}})

    assert resp.status_code == 200
    assert len(mgr.approved) == 0
    assert len(mgr.rejected) == 0


def test_webhook_invalid_callback_data():
    mgr = FakeApprovalManager()
    bot = FakeBot()
    app = _make_app(approval_manager=mgr, bot=bot)
    client = TestClient(app)

    resp = client.post("/telegram/webhook", json={
        "callback_query": {
            "id": "cb_3",
            "data": "garbage",
            "from": {"id": 999},
        }
    })

    assert resp.status_code == 200
    assert len(mgr.approved) == 0
    assert len(mgr.rejected) == 0


def test_webhook_not_configured():
    app = create_app(State(config=Config()))
    client = TestClient(app)

    resp = client.post("/telegram/webhook", json={
        "callback_query": {
            "id": "cb_4",
            "data": "approve:appr_789",
            "from": {"id": 999},
        }
    })

    assert resp.status_code == 200  # still 200 to not confuse Telegram
```

- [ ] **Run tests to verify they fail**

Run: `pytest polyarb/tests/test_webhook.py -v`
Expected: FAIL — `create_app() got an unexpected keyword argument 'approval_manager'`

### Step 2: Update server.py with webhook route

- [ ] **Modify server.py**

Update `create_app` in `polyarb/daemon/server.py` to accept `approval_manager` and `telegram_bot` parameters, and add the webhook route:

Add `approval_manager: Any = None` and `telegram_bot: Any = None` to the `create_app` signature.

Add this handler inside `create_app`:

```python
    async def telegram_webhook(request: Request) -> JSONResponse:
        body = await request.json()
        callback = body.get("callback_query")
        if not callback or not approval_manager or not telegram_bot:
            return JSONResponse({"ok": True})

        data = callback.get("data", "")
        callback_id = callback.get("id", "")

        if data.startswith("approve:"):
            approval_id = data.split(":", 1)[1]
            await approval_manager.handle_approve(approval_id)
            await telegram_bot.answer_callback(callback_id)
        elif data.startswith("reject:"):
            approval_id = data.split(":", 1)[1]
            await approval_manager.handle_reject(approval_id)
            await telegram_bot.answer_callback(callback_id)

        return JSONResponse({"ok": True})
```

Add to routes list:

```python
        Route("/telegram/webhook", telegram_webhook, methods=["POST"]),
```

- [ ] **Run webhook tests**

Run: `pytest polyarb/tests/test_webhook.py -v`
Expected: All 5 tests PASS.

### Step 3: Update engine.py with approval hook

- [ ] **Modify engine.py**

Update `run_scan_once` signature to accept optional `approval_manager`:

```python
async def run_scan_once(state: State, poly, kalshi, approval_manager=None) -> None:
```

After the existing WS broadcast block (after line 51), add:

```python
    # Approval manager hook (Telegram notifications)
    if approval_manager:
        await approval_manager.expire_stale()
        if new_matches:
            await approval_manager.on_new_matches(new_matches)
```

Update `run_scan_loop` signature similarly:

```python
async def run_scan_loop(state: State, poly, kalshi, approval_manager=None) -> None:
```

And pass it through:

```python
            await run_scan_once(state, poly, kalshi, approval_manager)
```

### Step 4: Update __main__.py to wire Telegram

- [ ] **Modify __main__.py**

After the kalshi_client setup block (after line 55), add Telegram setup:

```python
    # Optional Telegram notifications
    telegram_bot = None
    approval_manager = None
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if bot_token and chat_id:
        from polyarb.notifications.telegram import TelegramBot
        from polyarb.notifications.approval import ApprovalManager

        telegram_bot = TelegramBot(token=bot_token, chat_id=chat_id)
        approval_manager = ApprovalManager(
            state=state, bot=telegram_bot,
            kalshi_client=kalshi_client, config=config,
        )
        logger.info("Telegram notifications enabled (chat_id=%s)", chat_id)
    else:
        logger.info("Telegram not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")
```

Update the `create_app` call to pass the new params:

```python
    app = create_app(
        state,
        kalshi_client=kalshi_client,
        lifespan=lifespan,
        approval_manager=approval_manager,
        telegram_bot=telegram_bot,
    )
```

Update the `run_scan_loop` call in the lifespan to pass approval_manager:

```python
        scan_task = asyncio.get_event_loop().create_task(
            run_scan_loop(state, poly, kalshi, approval_manager)
        )
```

In the lifespan startup, after creating the scan task, register the Telegram webhook if configured:

```python
        if telegram_bot is not None:
            webhook_url = os.environ.get("TELEGRAM_WEBHOOK_URL", "")
            if webhook_url:
                await telegram_bot.set_webhook(f"{webhook_url}/telegram/webhook")
                logger.info("Telegram webhook registered: %s", webhook_url)
            else:
                logger.info("TELEGRAM_WEBHOOK_URL not set — set it to enable button callbacks")
```

Add telegram_bot cleanup in shutdown:

```python
        if telegram_bot is not None:
            await telegram_bot.close()
```

### Step 5: Run full suite and commit

- [ ] **Run all tests**

Run: `pytest -v`
Expected: All tests PASS. Existing engine tests still pass because `approval_manager` defaults to None.

- [ ] **Commit**

```bash
git add polyarb/daemon/engine.py polyarb/daemon/server.py polyarb/daemon/__main__.py polyarb/tests/test_webhook.py
git commit -m "Wire Telegram notifications into daemon

- Engine: hook approval_manager.expire_stale() and on_new_matches()
  into scan loop (optional, None when Telegram not configured)
- Server: add POST /telegram/webhook route for button callbacks
- __main__: create TelegramBot + ApprovalManager from env vars
- 5 webhook tests with fake approval manager"
```
