# Notification + Approval Loop — Design Spec

Roadmap step 6. Adds Telegram push notifications with inline approve/reject buttons and a timed approval flow for arb execution.

## Decisions

| Choice | Decision | Rationale |
|--------|----------|-----------|
| Notification channel | Telegram bot | Push to phone + inline approval buttons in one package |
| Approval model | Approve to execute, timeout = expire | Explicit approval required, no trades without user action |
| Timeout | Configurable, default 120s | Cross-platform arbs persist hours/days, 2 minutes is conservative |
| Re-alert | Only if profit improved | Avoids notification spam, catches widening spreads |
| Telegram transport | Webhook (not polling) | Starlette already runs an HTTP server, no extra thread needed |

## Architecture

```
Daemon scan loop finds new arb
        │
        ▼
Approval manager creates pending approval
        │
        ▼
Telegram bot sends alert with Approve/Reject buttons
        │
        ├── User taps Approve → re-check profit still positive → execute Kalshi leg → Telegram confirms result
        ├── User taps Reject → approval cancelled → Telegram confirms
        └── Timeout (120s) → approval expired
                │
                ▼
        Next scan: same arb still present, profit increased?
                ├── Yes → re-alert with updated profit
                └── No → stay silent
```

## Module Structure

### polyarb/notifications/telegram.py

Telegram Bot API client using `httpx.AsyncClient`.

```python
class TelegramBot:
    def __init__(self, token: str, chat_id: str, client: httpx.AsyncClient | None = None):
        ...

    async def send_alert(self, approval_id: str, match: MatchedPair) -> int:
        """Send arb alert with Approve/Reject inline buttons. Returns message_id."""

    async def edit_result(self, message_id: int, text: str) -> None:
        """Edit an existing message to show execution result."""

    async def edit_expired(self, message_id: int) -> None:
        """Edit message to show 'Expired'."""

    async def edit_rejected(self, message_id: int) -> None:
        """Edit message to show 'Rejected'."""

    async def close(self) -> None:
```

**Alert message format:**

```
🔔 New Cross-Platform Arb

Polymarket: Will BTC hit 100k?
  YES ask: $0.67

Kalshi: Bitcoin above 100k?
  YES ask: $0.62

Action: BUY YES on Kalshi + BUY NO on Polymarket
Profit/share: $0.0234

[Approve ✓]  [Reject ✗]
```

After execution, the message is edited in place:

```
✅ Executed — BUY YES on Kalshi @ $0.62
Status: resting, filled: 10
```

Or on timeout:

```
⏰ Expired (no response within 120s)
```

**Bot API methods used:**
- `sendMessage` with `reply_markup` (InlineKeyboardMarkup) for alert + buttons
- `editMessageText` for updating after approve/reject/timeout/execution
- `answerCallbackQuery` to acknowledge button press

**Callback data format:** Inline button callbacks carry `approve:{approval_id}` or `reject:{approval_id}` as the callback_data string.

**Config via env vars:**
- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `TELEGRAM_CHAT_ID` — your personal chat ID (get via @userinfobot or similar)

### polyarb/notifications/approval.py

Manages pending approvals, timeouts, and re-alert decisions.

```python
@dataclass
class PendingApproval:
    approval_id: str
    match_key: str              # poly_cid:kalshi_cid
    match_data: MatchedPair
    profit_at_alert: float
    telegram_message_id: int
    created_at: float           # time.monotonic()

class ApprovalManager:
    def __init__(self, state: State, bot: TelegramBot, kalshi_client, config: Config):
        self._pending: dict[str, PendingApproval] = {}
        self._alerted: dict[str, float] = {}   # match_key -> last_alerted_profit

    async def on_new_matches(self, new_matches: list[MatchedPair]) -> None:
        """Called by engine after each scan. Sends alerts for qualifying matches."""

    async def handle_approve(self, approval_id: str) -> str:
        """Execute the trade. Returns result description."""

    async def handle_reject(self, approval_id: str) -> None:
        """Cancel a pending approval."""

    async def expire_stale(self) -> None:
        """Called periodically. Expires approvals older than config.approval_timeout."""

    def should_alert(self, match: MatchedPair) -> bool:
        """True if match has never been alerted, or profit improved since last alert."""
```

**Re-alert logic in `should_alert()`:**
- Compute match key as `poly_cid:kalshi_cid`
- If key not in `_alerted` dict → alert (first time)
- If key in `_alerted` and current `best_arb.profit > _alerted[key]` → alert (profit improved)
- Otherwise → skip

**On approve:**
1. Look up PendingApproval by approval_id
2. Find the match in current `state.matches` (it may have been updated with fresh prices)
3. Re-check that `best_arb.profit > 0` (prices may have moved against us)
4. If still profitable: execute via `kalshi_client.create_order()`
5. Edit Telegram message with result
6. If no longer profitable: edit message with "Arb no longer profitable, skipped"

**Timeout expiry:**
- `expire_stale()` is called each scan cycle (inside the engine loop)
- For each pending approval older than `config.approval_timeout`: edit Telegram message to "Expired", remove from pending

### polyarb/daemon/engine.py (modified)

After existing scan logic, add hook:

```python
if approval_manager:
    await approval_manager.expire_stale()
    await approval_manager.on_new_matches(new_matches)
```

The approval manager is optional — if Telegram is not configured, it's None and the engine works exactly as before.

### polyarb/daemon/server.py (modified)

Add one route:

```
POST /telegram/webhook — receives Telegram callback queries
```

Handler:
1. Parse the incoming Telegram update JSON
2. Extract `callback_query.data` (e.g. `"approve:abc123"` or `"reject:abc123"`)
3. Route to `approval_manager.handle_approve()` or `handle_reject()`
4. Respond with `answerCallbackQuery` to dismiss the button spinner
5. Return 200 OK to Telegram

Also add `GET /telegram/setup` that returns the webhook URL to configure, for convenience.

### polyarb/daemon/__main__.py (modified)

Add Telegram setup:
- Read `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from env vars
- If both present: create `TelegramBot`, create `ApprovalManager`, pass to engine and server
- If not set: log info message, daemon runs without notifications (existing behavior)

On startup: register the Telegram webhook URL with `setWebhook` API call (`https://{host}:{port}/telegram/webhook`). This requires the daemon to be reachable from Telegram's servers — works when deployed, not from localhost. For local dev, users can use ngrok or similar.

### Config additions

Add to `polyarb/config.py`:

```python
approval_timeout: float = 120.0
telegram_enabled: bool = False
```

`telegram_enabled` is set to True automatically when bot token is detected, not manually configured.

### What stays unchanged

- WS push alerts to CLI still fire (Telegram is additive, not a replacement)
- CLI `execute` command still works for manual execution
- CLI `scan` / `detail` commands unchanged
- All existing tests unaffected
- Daemon works identically when TELEGRAM_BOT_TOKEN is not set

## Testing

New test files:
- `test_telegram.py` — Test TelegramBot with mocked httpx (verify message format, button callback data, edit calls)
- `test_approval.py` — Test ApprovalManager: alert flow, approve/reject handlers, timeout expiry, re-alert logic (profit improved vs same), profit re-check before execution

Existing test files unchanged.

## Dependencies

No new dependencies. Telegram Bot API is plain HTTPS — `httpx` (already installed) handles it.

## Implementation Steps (one commit each)

1. **Config + TelegramBot** — Add approval_timeout to Config, create notifications/ package with telegram.py, tests
2. **ApprovalManager** — Create approval.py with pending tracking, approve/reject/expire/re-alert logic, tests
3. **Daemon integration** — Wire into engine.py, server.py, __main__.py. Add /telegram/webhook route. Tests for webhook handler.
