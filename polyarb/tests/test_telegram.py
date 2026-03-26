"""Tests for TelegramBot notification client."""

from __future__ import annotations

import json

import httpx
import pytest

from polyarb.matching.matcher import MatchedPair
from polyarb.models import ArbType, Market, Opportunity, Side, Token
from polyarb.notifications.telegram import TelegramBot


# ── Helpers ─────────────────────────────────────────────────


def _make_market(
    cid: str, question: str, platform: str, yes_ask: float
) -> Market:
    no_ask = round(1.0 - yes_ask, 4)
    return Market(
        condition_id=cid,
        question=question,
        yes_token=Token("y-" + cid, Side.YES, yes_ask, yes_ask - 0.01, yes_ask),
        no_token=Token("n-" + cid, Side.NO, no_ask, no_ask - 0.01, no_ask),
        platform=platform,
    )


def _make_pair() -> MatchedPair:
    poly = _make_market("poly-1", "Will BTC hit $100k?", "polymarket", 0.55)
    kalshi = _make_market("kalshi-1", "Bitcoin above $100k?", "kalshi", 0.48)
    return MatchedPair(poly_market=poly, kalshi_market=kalshi, confidence=0.85)


# ── Mock transport / recorder ──────────────────────────────


class _Recorder:
    """Captures requests made through httpx.MockTransport."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path

        if path.endswith("/sendMessage"):
            body = {"ok": True, "result": {"message_id": 42}}
        else:
            body = {"ok": True, "result": {}}

        return httpx.Response(200, json=body)

    def last_json(self) -> dict:
        return json.loads(self.requests[-1].content)

    def last_path(self) -> str:
        return self.requests[-1].url.path


# ── Tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_alert_returns_message_id():
    rec = _Recorder()
    transport = httpx.MockTransport(rec.handler)
    client = httpx.AsyncClient(transport=transport)
    bot = TelegramBot(token="tok123", chat_id="999", client=client)

    pair = _make_pair()
    msg_id = await bot.send_alert("abc-1", pair)

    assert msg_id == 42
    payload = rec.last_json()
    assert payload["chat_id"] == "999"
    assert "BTC" in payload["text"] or "Bitcoin" in payload["text"]
    # Verify inline keyboard with approve/reject buttons
    markup = payload["reply_markup"]
    buttons = markup["inline_keyboard"]
    callbacks = [btn["callback_data"] for row in buttons for btn in row]
    assert "approve:abc-1" in callbacks
    assert "reject:abc-1" in callbacks


@pytest.mark.asyncio
async def test_send_alert_message_contains_profit():
    rec = _Recorder()
    transport = httpx.MockTransport(rec.handler)
    client = httpx.AsyncClient(transport=transport)
    bot = TelegramBot(token="tok123", chat_id="999", client=client)

    pair = _make_pair()
    await bot.send_alert("abc-2", pair)

    payload = rec.last_json()
    text_lower = payload["text"].lower()
    assert "profit" in text_lower


@pytest.mark.asyncio
async def test_edit_result():
    rec = _Recorder()
    transport = httpx.MockTransport(rec.handler)
    client = httpx.AsyncClient(transport=transport)
    bot = TelegramBot(token="tok123", chat_id="999", client=client)

    await bot.edit_result(42, "Trade executed successfully")

    assert rec.last_path().endswith("/editMessageText")
    payload = rec.last_json()
    assert payload["message_id"] == 42
    assert payload["chat_id"] == "999"
    assert "Trade executed successfully" in payload["text"]


@pytest.mark.asyncio
async def test_edit_expired():
    rec = _Recorder()
    transport = httpx.MockTransport(rec.handler)
    client = httpx.AsyncClient(transport=transport)
    bot = TelegramBot(token="tok123", chat_id="999", client=client)

    await bot.edit_expired(42)

    assert rec.last_path().endswith("/editMessageText")
    payload = rec.last_json()
    text_lower = payload["text"].lower()
    assert "expired" in text_lower


@pytest.mark.asyncio
async def test_edit_rejected():
    rec = _Recorder()
    transport = httpx.MockTransport(rec.handler)
    client = httpx.AsyncClient(transport=transport)
    bot = TelegramBot(token="tok123", chat_id="999", client=client)

    await bot.edit_rejected(42)

    assert rec.last_path().endswith("/editMessageText")
    payload = rec.last_json()
    text_lower = payload["text"].lower()
    assert "rejected" in text_lower


@pytest.mark.asyncio
async def test_answer_callback():
    rec = _Recorder()
    transport = httpx.MockTransport(rec.handler)
    client = httpx.AsyncClient(transport=transport)
    bot = TelegramBot(token="tok123", chat_id="999", client=client)

    await bot.answer_callback("cb-777")

    assert rec.last_path().endswith("/answerCallbackQuery")
    payload = rec.last_json()
    assert payload["callback_query_id"] == "cb-777"


@pytest.mark.asyncio
async def test_set_webhook():
    rec = _Recorder()
    transport = httpx.MockTransport(rec.handler)
    client = httpx.AsyncClient(transport=transport)
    bot = TelegramBot(token="tok123", chat_id="999", client=client)

    await bot.set_webhook("https://example.com/hook")

    assert rec.last_path().endswith("/setWebhook")
    payload = rec.last_json()
    assert payload["url"] == "https://example.com/hook"


@pytest.mark.asyncio
async def test_send_digest():
    rec = _Recorder()
    client = httpx.AsyncClient(transport=httpx.MockTransport(rec.handler))
    bot = TelegramBot(token="tok123", chat_id="999", client=client)

    def _mkt(cid):
        return Market(
            condition_id=cid,
            question=f"Will {cid} happen?",
            yes_token=Token(
                token_id=f"{cid}:y", side=Side.YES, midpoint=0.6,
                best_bid=0.59, best_ask=0.61,
            ),
            no_token=Token(
                token_id=f"{cid}:n", side=Side.NO, midpoint=0.4,
                best_bid=0.39, best_ask=0.41,
            ),
        )

    opps = [
        Opportunity(
            arb_type=ArbType.SINGLE_UNDERPRICE,
            markets=(_mkt("a"),),
            expected_profit_per_share=0.03,
        ),
        Opportunity(
            arb_type=ArbType.SINGLE_UNDERPRICE,
            markets=(_mkt("b"),),
            expected_profit_per_share=0.01,
        ),
    ]

    msg_id = await bot.send_digest(opps, limit=20)

    assert msg_id == 42
    assert len(rec.requests) == 1
    body = json.loads(rec.requests[0].content)
    text = body["text"]
    assert "Digest" in text
    assert "Will a happen?" in text
    # Verify sorted by profit (a=0.03 before b=0.01)
    assert text.index("Will a happen?") < text.index("Will b happen?")
    await bot.close()


@pytest.mark.asyncio
async def test_send_digest_empty():
    rec = _Recorder()
    client = httpx.AsyncClient(transport=httpx.MockTransport(rec.handler))
    bot = TelegramBot(token="tok123", chat_id="999", client=client)

    msg_id = await bot.send_digest([], limit=20)

    assert msg_id == 0
    assert len(rec.requests) == 0
    await bot.close()
