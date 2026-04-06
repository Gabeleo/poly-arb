"""ApprovalManager: tracks pending Telegram-based approvals for arb execution."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from polyarb.analysis.costs import compute_arb
from polyarb.config import Config
from polyarb.matching.matcher import MatchedPair
from polyarb.notifications.base import Notifier


@dataclass
class PendingApproval:
    approval_id: str
    match_key: str  # "poly_cid:kalshi_cid"
    match_data: MatchedPair
    profit_at_alert: float
    telegram_message_id: int
    created_at: float  # time.monotonic()


class ApprovalManager:
    """Manages the approve/reject lifecycle for cross-platform arb alerts.

    Tracks pending approvals sent via Telegram, handles user responses,
    and expires stale entries.
    """

    def __init__(self, state, bot: Notifier, kalshi_client, config: Config) -> None:
        self._state = state
        self._bot = bot
        self._kalshi_client = kalshi_client
        self._config = config
        self._pending: dict[str, PendingApproval] = {}
        self._alerted: dict[str, float] = {}  # match_key -> last_alerted_profit

    # ── Alerting logic ─────────────────────────────────────

    def fee_adjusted_profit(self, match: MatchedPair) -> float:
        """Compute net profit after fees using the cost model."""
        pm = match.poly_market
        km = match.kalshi_market
        arb = compute_arb(
            pm.yes_token.best_ask, pm.no_token.best_ask,
            km.yes_token.best_ask, km.no_token.best_ask,
        )
        return arb.net_profit if arb else 0.0

    def should_alert(self, match: MatchedPair) -> bool:
        """Decide whether a match warrants a new Telegram alert.

        Uses the fee-adjusted cost model — only alerts on genuinely
        profitable opportunities after Polymarket taker fees and
        Kalshi entry fees.
        """
        profit = self.fee_adjusted_profit(match)
        if profit <= 0:
            return False

        key = f"{match.poly_market.condition_id}:{match.kalshi_market.condition_id}"

        if key not in self._alerted:
            return True

        if profit > self._alerted[key]:
            return True

        return False

    async def on_new_matches(self, new_matches: list[MatchedPair]) -> None:
        """Send Telegram alerts for matches that pass ``should_alert``."""
        for match in new_matches:
            if not self.should_alert(match):
                continue

            approval_id = uuid.uuid4().hex[:12]
            message_id = await self._bot.send_alert(approval_id, match)

            key = f"{match.poly_market.condition_id}:{match.kalshi_market.condition_id}"
            profit = self.fee_adjusted_profit(match)

            self._pending[approval_id] = PendingApproval(
                approval_id=approval_id,
                match_key=key,
                match_data=match,
                profit_at_alert=profit,
                telegram_message_id=message_id,
                created_at=time.monotonic(),
            )
            self._alerted[key] = profit

    # ── Approval / rejection handlers ──────────────────────

    async def handle_approve(self, approval_id: str) -> str:
        """Block execution until both platform legs are implemented.

        Returns a human-readable result description.
        """
        pending = self._pending.pop(approval_id, None)
        if pending is None:
            return "Approval not found or already expired"

        msg = "Execution disabled — Polymarket CLOB leg not yet implemented"
        await self._bot.edit_result(pending.telegram_message_id, msg)
        return msg

    async def handle_reject(self, approval_id: str) -> None:
        """Cancel a pending approval and update the Telegram message."""
        pending = self._pending.pop(approval_id, None)
        if pending is not None:
            await self._bot.edit_rejected(pending.telegram_message_id)

    # ── Expiry ─────────────────────────────────────────────

    async def expire_stale(self) -> None:
        """Remove approvals that have exceeded ``config.approval_timeout``."""
        now = time.monotonic()
        expired_ids: list[str] = []

        for aid, pending in self._pending.items():
            if now - pending.created_at >= self._config.approval_timeout:
                expired_ids.append(aid)

        for aid in expired_ids:
            pending = self._pending.pop(aid)
            await self._bot.edit_expired(pending.telegram_message_id)

        # Prune _alerted for matches no longer present
        current_keys = {
            f"{m.poly_market.condition_id}:{m.kalshi_market.condition_id}"
            for m in self._state.matches
        }
        self._alerted = {k: v for k, v in self._alerted.items() if k in current_keys}
