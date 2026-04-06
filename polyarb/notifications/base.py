"""Notification protocol for arb alerts and approval flow."""

from __future__ import annotations

from typing import Protocol

from polyarb.matching.matcher import MatchedPair
from polyarb.models import Opportunity


class Notifier(Protocol):
    """Protocol for sending alerts and managing approval message state.

    Implementations: TelegramNotifier (via TelegramBot), future Slack/email.
    """

    async def send_alert(self, approval_id: str, match: MatchedPair) -> int:
        """Send an alert for a new arb match. Returns a message ID for later edits."""
        ...

    async def edit_result(self, message_id: int, text: str) -> None:
        """Update alert with execution result."""
        ...

    async def edit_expired(self, message_id: int) -> None:
        """Mark alert as expired."""
        ...

    async def edit_rejected(self, message_id: int) -> None:
        """Mark alert as rejected."""
        ...

    async def send_digest(self, opps: list[Opportunity], limit: int = 20) -> int:
        """Send a digest of top opportunities. Returns a message ID."""
        ...

    async def close(self) -> None:
        """Clean up resources."""
        ...
