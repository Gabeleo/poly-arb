# Polyarb Codebase Overview

Cross-platform prediction market arbitrage system. Core thesis: do arb opportunities exist between Kalshi and Polymarket that persist long enough to act on and profit from?

## Directory Structure

```
polyarb/
├── __init__.py
├── __main__.py              # CLI entry point (daemon/client/mock modes)
├── models.py                # Frozen dataclasses: Token, Market, Event, Opportunity, Order, OrderSet
├── config.py                # Config dataclass (min_profit, max_prob, scan_interval, etc.)
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
│   ├── matcher.py           # find_matches(), generate_all_pairs(), MatchedPair
│   ├── normalize.py         # Text normalization, tokenization, year extraction
│   └── encoder_client.py    # Async client to cross-encoder ML scoring service
│
├── daemon/                  # Production system
│   ├── __main__.py          # Entry: python -m polyarb.daemon
│   ├── engine.py            # Scan loop: fetch → match → detect → push
│   ├── server.py            # Starlette REST API + WebSocket
│   └── state.py             # In-memory state: matches, opps, dedup, WS clients
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
├── execution/               # Order execution
│   ├── orders.py            # build_order_set() — Opportunity → OrderSet
│   ├── executor.py          # Executor protocol, MockExecutor, LiveExecutor (stub)
│   ├── kalshi.py            # KalshiAuth (RSA-PSS), KalshiExecutor
│   └── async_kalshi.py      # Async httpx Kalshi client
│
├── notifications/           # Alerts and approval flow
│   ├── telegram.py          # TelegramBot: alerts, digests, callbacks
│   └── approval.py          # ApprovalManager: Telegram-based trade approval
│
├── client/                  # Thin CLI client for daemon
│   ├── __main__.py          # Entry: python -m polyarb.client
│   ├── api.py               # DaemonClient REST wrapper
│   ├── cli.py               # Interactive cmd.Cmd shell
│   └── ws_listener.py       # Background WebSocket listener
│
├── alerts/                  # Alert interface
│   ├── base.py              # Alerter protocol
│   └── console.py           # ConsoleAlerter (ANSI output)
│
└── tests/                   # 166 tests across 20 modules
    ├── test_single.py       # Single-market detection
    ├── test_multi.py        # Multi-market detection
    ├── test_orders.py       # Order building
    ├── test_matching.py     # Cross-platform matcher + generate_all_pairs
    ├── test_encoder_client.py
    ├── test_daemon_engine.py
    ├── test_daemon_state.py
    ├── test_server.py
    ├── test_client_api.py
    ├── test_async_providers.py
    ├── test_kalshi_exec.py
    ├── test_telegram.py
    ├── test_webhook.py
    ├── test_approval.py
    ├── test_serialization.py
    ├── test_recorder_db.py
    ├── test_costs.py
    ├── test_lifetime.py
    ├── test_backtest.py
    └── generate_mock_db.py  # 7-day mock dataset generator

Project root/
├── pyproject.toml           # Package config, deps, pytest settings
├── CLAUDE.md                # Project instructions and architecture
├── Dockerfile               # Python 3.13 slim, exposes 8080
├── compose.yaml             # Docker Compose: encoder + daemon + client
├── .env                     # Telegram/Kalshi credentials
└── encoder/                 # Cross-encoder ML sidecar
    ├── Dockerfile
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

Two-stage pipeline: cheap token-based filtering → expensive cross-encoder verification.

- **Token matcher** (`find_matches`): Jaccard + containment + SequenceMatcher. Year-mismatch hard filter. 1:1 best-match-per-Poly constraint.
- **All-pairs generator** (`generate_all_pairs`): Full cartesian product minus year mismatches, ranked by SequenceMatcher. Fed to cross-encoder when available.
- **Cross-encoder** (`encoder_client`): Hits sidecar running `cross-encoder/stsb-TinyBERT-L-4`. Normalizes STS-B scores to 0.0-1.0.

### 3. Daemon

Production scan loop: fetches both platforms, matches markets, detects price deltas, pushes to Telegram and WebSocket.

- `run_scan_once()`: fetch → match → detect → notify
- REST API: `/status`, `/matches`, `/opportunities`, `/config`, `/execute/{id}`
- WebSocket: real-time push to connected clients

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

- **Kalshi**: RSA-PSS signed auth, order placement, partial-failure recovery (cancels placed legs on failure)
- **Polymarket**: CLOB stub — **not built until backtest shows positive P&L**

### 7. Notifications

- Telegram: alerts on new opportunities, `/scan` command for top arbs, hourly digest
- Approval flow: Telegram inline buttons for trade approval/rejection with timeout

## Key Design Decisions

- **Protocol-based**: `DataProvider`, `AsyncDataProvider`, `Executor` use `typing.Protocol`
- **Immutable models**: All domain objects are frozen dataclasses
- **Async daemon, sync CLI**: Daemon uses `asyncio` + `httpx`. CLI uses `urllib.request`
- **Enter and hold**: Prediction market arbs lock profit at entry — buy complementary sides, hold to settlement
- **1 contract per window**: Backtest enters once per arb window, not every scan

## Running

```bash
# Install
pip install -e ".[dev]"

# Tests (166 tests)
pytest

# Recorder (collects live data)
python -m polyarb.recorder -v

# Daemon
python -m polyarb.daemon --host 0.0.0.0 --port 8080 --interval 10

# Docker
docker compose run --build polyarb --poly

# Generate mock dataset (7 days, ~1.1M rows)
python -m polyarb.tests.generate_mock_db
```

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `KALSHI_API_KEY` | Kalshi authenticated execution |
| `KALSHI_KEY_FILE` | RSA private key path |
| `ENCODER_URL` | Cross-encoder service endpoint |
| `TELEGRAM_BOT_TOKEN` | Telegram alerts |
| `TELEGRAM_CHAT_ID` | Telegram target chat |
| `TELEGRAM_WEBHOOK_URL` | Telegram webhook endpoint |
