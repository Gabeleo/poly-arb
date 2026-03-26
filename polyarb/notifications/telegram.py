"""Telegram Bot API client for arbitrage alerts and approval flow."""

from __future__ import annotations

import httpx

from polyarb.matching.matcher import MatchedPair


def _format_alert(match: MatchedPair) -> str:
    """Build the alert message body from a MatchedPair."""
    profit, _, kalshi_desc, poly_desc, _ = match.best_arb
    poly = match.poly_market
    kalshi = match.kalshi_market

    return (
        "\U0001f514 New Cross-Platform Arb\n"
        "\n"
        f"Polymarket: {poly.question}\n"
        f"  YES ask: ${poly.yes_token.best_ask}\n"
        "\n"
        f"Kalshi: {kalshi.question}\n"
        f"  YES ask: ${kalshi.yes_token.best_ask}\n"
        "\n"
        f"Action: {kalshi_desc} + {poly_desc}\n"
        f"Profit/share: ${profit}"
    )


def _inline_keyboard(approval_id: str) -> dict:
    """Build Telegram InlineKeyboardMarkup with Approve / Reject buttons."""
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Approve \u2713",
                    "callback_data": f"approve:{approval_id}",
                },
                {
                    "text": "Reject \u2717",
                    "callback_data": f"reject:{approval_id}",
                },
            ]
        ]
    }


class TelegramBot:
    """Async Telegram Bot API client for sending alerts and handling
    approval callbacks."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = f"https://api.telegram.org/bot{token}"
        self._chat_id = chat_id
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient()
            self._owns_client = True

    async def _post(self, method: str, data: dict) -> dict:
        """POST JSON to ``{base}/{method}`` and return the parsed response."""
        resp = await self._client.post(
            f"{self._base}/{method}",
            json=data,
        )
        resp.raise_for_status()
        return resp.json()

    async def send_alert(self, approval_id: str, match: MatchedPair) -> int:
        """Send an alert message with inline approve/reject buttons.

        Returns the Telegram ``message_id``.
        """
        text = _format_alert(match)
        result = await self._post(
            "sendMessage",
            {
                "chat_id": self._chat_id,
                "text": text,
                "reply_markup": _inline_keyboard(approval_id),
            },
        )
        return result["result"]["message_id"]

    async def edit_result(self, message_id: int, text: str) -> None:
        """Replace alert text with an execution result summary."""
        await self._post(
            "editMessageText",
            {
                "chat_id": self._chat_id,
                "message_id": message_id,
                "text": f"\u2705 {text}",
            },
        )

    async def edit_expired(self, message_id: int) -> None:
        """Mark the alert as expired."""
        await self._post(
            "editMessageText",
            {
                "chat_id": self._chat_id,
                "message_id": message_id,
                "text": "\u23f0 Expired \u2014 approval window closed",
            },
        )

    async def edit_rejected(self, message_id: int) -> None:
        """Mark the alert as rejected."""
        await self._post(
            "editMessageText",
            {
                "chat_id": self._chat_id,
                "message_id": message_id,
                "text": "\u274c Rejected by operator",
            },
        )

    async def answer_callback(self, callback_query_id: str) -> None:
        """Acknowledge a callback query (removes the spinner)."""
        await self._post(
            "answerCallbackQuery",
            {"callback_query_id": callback_query_id},
        )

    async def set_webhook(self, url: str) -> None:
        """Register a webhook URL with the Telegram Bot API."""
        await self._post("setWebhook", {"url": url})

    async def close(self) -> None:
        """Close the underlying HTTP client if we own it."""
        if self._owns_client:
            await self._client.aclose()
