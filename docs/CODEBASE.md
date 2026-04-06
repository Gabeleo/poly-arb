# Polyarb Codebase Overview

Cross-platform prediction market arbitrage system. Core thesis: do arb opportunities exist between Kalshi and Polymarket that persist long enough to act on and profit from?

## Directory Structure

```
polyarb/
├── __init__.py
├── __main__.py              # CLI entry point (daemon/client/mock modes)
├── models.py                # Frozen dataclasses: Token, Market, Event, Opportunity, Order, OrderSet
├── config.py                # Config dataclass with __post_init__ validation
├── colors.py                # ANSI color constants
│
├── data/                    # Market data providers
│   ├── base.py              # DataProvider & AsyncDataProvider protocols, group_events()
│   ├── mock.py              # MockDataProvider (synthetic markets with drift)
│   ├── async_live.py        # Polymarket Gamma API (public, no auth)
│   └── async_kalshi.py      # Kalshi Trading API v2 (public reads, no auth)
│
├── engine/                  # Single-platform arbitrage detection
│   ├── single.py            # detect_single() — YES+NO imbalances
│   └── multi.py             # detect_multi() — neg_risk event mispricing
│
├── matching/                # Cross-platform market matching
│   ├── matcher.py           # find_matches(), generate_all_pairs(), MatchedPair,
│   │                        # MatchingStrategy protocol, TokenMatcher, EncoderMatcher
│   ├── normalize.py         # Text normalization, tokenization, year extraction
│   └── encoder_client.py    # Async client to cross-encoder ML scoring service
│
├── daemon/                  # Production system
│   ├── __main__.py          # Entry: python -m polyarb.daemon
│   ├── engine.py            # Scan loop with circuit breaker, fetch timeouts,
│   │                        # graceful shutdown. Sub-functions: _fetch_markets,
│   │                        # _match_markets, _detect_opportunities, _publish_results
│   ├── routes.py            # Route handlers (deps accessed via request.app.state)
│   ├── server.py            # Starlette app factory + ApiKeyMiddleware
│   └── state.py             # In-memory state: matches, opps, dedup, WS clients,
│                            # last_scan_error tracking
│
├── recorder/                # Market snapshot recording
│   ├── __main__.py          # Entry: python -m polyarb.recorder
│   ├── db.py                # RecorderDB: SQLite schema, filtered inserts
│   └── recorder.py          # Async fetch loop, stores to SQLite every 30s
│
├── analysis/                # Validation sequence (Record → Cost → Lifetime → Backtest)
│   ├── costs.py             # Fee model: Polymarket taker fees, Kalshi entry fees
│   ├── lifetime.py          # Arb window detection, duration statistics
│   └── backtest.py          # Replay scans, simulate trades, track P&L + capital
│
├── execution/               # Order execution (BLOCKED until backtest shows +P&L)
│   ├── orders.py            # build_order_set() — Opportunity → OrderSet
│   ├── executor.py          # Executor protocol, MockExecutor, LiveExecutor (stub)
│   ├── kalshi.py            # KalshiAuth (RSA-PSS), KalshiExecutor
│   └── async_kalshi.py      # Async httpx Kalshi client
│
├── notifications/           # Alerts and approval flow
│   ├── base.py              # Notifier protocol (decoupled from Telegram)
│   ├── telegram.py          # TelegramBot: implements Notifier
│   └── approval.py          # ApprovalManager: fee-aware alerting, execution blocked
│
├── client/                  # Thin CLI client for daemon
│   ├── __main__.py          # Entry: python -m polyarb.client (--api-key support)
│   ├── api.py               # DaemonClient REST wrapper (sends X-API-Key header)
│   ├── cli.py               # Interactive cmd.Cmd shell
│   └── ws_listener.py       # Background WebSocket listener (with error logging)
│
├── alerts/                  # Alert interface
│   ├── base.py              # Alerter protocol
│   └── console.py           # ConsoleAlerter (ANSI output)
│
└── tests/                   # 217 tests across 20+ modules
    ├── test_single.py       # Single-market detection
    ├── test_multi.py        # Multi-market detection
    ├── test_orders.py       # Order building
    ├── test_matching.py     # Cross-platform matcher + generate_all_pairs
    ├── test_encoder_client.py
    ├── test_daemon_engine.py  # Scan loop, circuit breaker, provider timeout
    ├── test_daemon_state.py
    ├── test_server.py       # Routes, auth middleware, health check, execution block
    ├── test_client_api.py
    ├── test_async_providers.py
    ├── test_kalshi_exec.py
    ├── test_telegram.py
    ├── test_webhook.py
    ├── test_approval.py     # Fee-aware alerting, execution block
    ├── test_serialization.py
    ├── test_recorder_db.py
    ├── test_costs.py
    ├── test_lifetime.py
    ├── test_backtest.py
    └── generate_mock_db.py  # 7-day mock dataset generator

Project root/
├── pyproject.toml           # Package config, deps, pytest settings
├── CLAUDE.md                # Project instructions and architecture
├── Dockerfile               # Python 3.13 slim, non-root user, exposes 8080
├── compose.yaml             # Docker Compose: encoder + daemon (healthchecked) + client
├── .env                     # Telegram/Kalshi/API credentials
└── encoder/                 # Cross-encoder ML sidecar
    ├── Dockerfile           # Python 3.11 slim, non-root user
    ├── app.py               # FastAPI service running stsb-TinyBERT-L-4
    └── requirements.txt
```

## System Layers

### 1. Data Providers

Fetch active binary markets from both platforms. Protocol-based — sync for CLI, async for daemon/recorder.

| Provider | API | Auth | Volume Unit |
|----------|-----|------|-------------|
| Polymarket | Gamma API (`gamma-api.polymarket.com`) | None | Dollars |
| Kalshi | Trading API v2 (`api.elections.kalshi.com`) | None (reads) | Contracts |

### 2. Cross-Platform Matching

Two-stage pipeline: cheap token-based filtering → expensive cross-encoder verification. Unified behind a `MatchingStrategy` protocol with `TokenMatcher` and `EncoderMatcher` implementations.

- **Token matcher** (`find_matches`): Jaccard + containment + SequenceMatcher. Year-mismatch hard filter. 1:1 best-match-per-Poly constraint.
- **All-pairs generator** (`generate_all_pairs`): Full cartesian product minus year mismatches, ranked by SequenceMatcher. Fed to cross-encoder when available.
- **Cross-encoder** (`encoder_client`): Hits sidecar running `cross-encoder/stsb-TinyBERT-L-4`. Normalizes STS-B scores to 0.0-1.0.

### 3. Daemon

Production scan loop: fetches both platforms, matches markets, detects price deltas, pushes to Telegram and WebSocket.

- `run_scan_once()` orchestrates 4 sub-steps: `_fetch_markets` → `_match_markets` → `_detect_opportunities` → `_publish_results`
- **Fetch timeouts**: Each provider call wrapped in `asyncio.wait_for(..., timeout=30s)`. Providers fail independently.
- **Circuit breaker**: After 5 consecutive provider failures, exponential backoff (10s → 20s → ... → 5min cap). Resets on success.
- **Graceful shutdown**: `stop_event` signals the loop to finish the current scan before exiting. Falls back to cancellation after timeout.
- REST API: `/health`, `/status`, `/matches`, `/opportunities`, `/config`, `/execute/{id}`
- WebSocket: real-time push to connected clients
- **API key auth**: `ApiKeyMiddleware` checks `X-API-Key` header on `POST /config`, `POST /execute/`. WebSocket checks `?api_key=` query param.
- **Execution blocked**: Both `/execute` and `handle_approve` return 503 until Polymarket CLOB leg is implemented. Prevents naked single-leg exposure.

### 4. Recorder

Standalone process that persists raw market snapshots to SQLite every 30 seconds.

- Filters: volume >= $10k AND volume_24h > 0
- Schema: separate tables for each platform with bid/ask/volume per market
- Dedup: `UNIQUE(scan_ts, condition_id/ticker)`

### 5. Analysis Pipeline (Validation Sequence)

Each step gates the next. If any answer is negative, stop.

| Step | Module | Question |
|------|--------|----------|
| **Record** | `recorder/` | Persist every scan cycle's prices to SQLite |
| **Cost Model** | `analysis/costs.py` | Is the delta profitable after Polymarket taker fees + Kalshi entry fees? |
| **Lifetime** | `analysis/lifetime.py` | How long does a profitable delta persist? |
| **Backtest** | `analysis/backtest.py` | Replaying all scans: what's the total P&L? |

**Cost model findings**: At mid-range prices (~$0.50), minimum profitable raw delta is ~3.3 cents. Polymarket fee = `fee_rate * p * (1-p)` (varies 3-7.2% by category). Kalshi fee = `min(0.07 * min(p, 1-p), $0.02)`.

### 6. Execution

**Currently blocked** — execution is disabled until the backtest on live data shows positive P&L and Polymarket CLOB execution is built.

- **Kalshi**: RSA-PSS signed auth, order placement, partial-failure recovery (cancels placed legs on failure)
- **Polymarket**: CLOB stub — **not built until backtest shows positive P&L**
- **Fee model wired into approval**: `ApprovalManager.should_alert()` uses `compute_arb()` from `analysis/costs.py` — only genuinely profitable-after-fees matches trigger alerts.

### 7. Notifications

Decoupled behind a `Notifier` protocol (`notifications/base.py`). `ApprovalManager` accepts any `Notifier`, not just `TelegramBot`.

- Telegram: alerts on new opportunities, `/scan` command for top arbs, hourly digest
- Approval flow: Telegram inline buttons for trade approval/rejection with timeout
- Execution on approve is blocked (returns "Polymarket CLOB not yet implemented")

## Key Design Decisions

- **Protocol-based**: `DataProvider`, `AsyncDataProvider`, `Executor`, `Notifier`, `MatchingStrategy` use `typing.Protocol`
- **Immutable models**: All domain objects are frozen dataclasses
- **Config validation**: `Config.__post_init__` validates all fields at construction; `POST /config` builds a trial Config before applying
- **Async daemon, sync CLI**: Daemon uses `asyncio` + `httpx`. CLI uses `httpx` sync client
- **Enter and hold**: Prediction market arbs lock profit at entry — buy complementary sides, hold to settlement
- **1 contract per window**: Backtest enters once per arb window, not every scan
- **Route extraction**: Handlers in `routes.py`, dependencies accessed via `request.app.state` — no closure capture

## Running

```bash
# Install
pip install -e ".[dev]"

# Tests (217 tests)
pytest

# Recorder (collects live data)
python -m polyarb.recorder -v

# Daemon
python -m polyarb.daemon --host 0.0.0.0 --port 8080 --interval 10

# Client (with auth)
python -m polyarb.client --url http://127.0.0.1:8080 --api-key <key>

# Docker
docker compose run --build polyarb --poly

# Generate mock dataset (7 days, ~1.1M rows)
python -m polyarb.tests.generate_mock_db
```

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `POLYARB_API_KEY` | API key for daemon protected endpoints (`POST /config`, `/execute`, WS) |
| `KALSHI_API_KEY` | Kalshi authenticated execution |
| `KALSHI_KEY_FILE` | RSA private key path |
| `ENCODER_URL` | Cross-encoder service endpoint |
| `TELEGRAM_BOT_TOKEN` | Telegram alerts |
| `TELEGRAM_CHAT_ID` | Telegram target chat |
| `TELEGRAM_WEBHOOK_URL` | Telegram webhook endpoint |
