# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Polyarb is a cross-platform prediction market arbitrage system. The core thesis: **do arb opportunities exist between Kalshi and Polymarket that persist long enough to act on and profit from?** Single-platform detection (YES+NO imbalances) is supporting infrastructure — cross-platform price comparison on semantically matched markets is the goal.

## Guidelines

Self-verify all work by testing end to end. Do not return control until requirements are met and working as expected.

When `/stash` is referenced, it refers to `docs/stash/` in this repo.

## Commands

```bash
# Install (editable with dev deps)
pip install -e ".[dev]"

# Run tests
pytest

# Run a single test file
pytest polyarb/tests/test_single.py

# Run a single test
pytest polyarb/tests/test_single.py::test_underprice_detected -v

# Run CLI (mock data / playground)
python -m polyarb

# Run CLI (live Polymarket data)
python -m polyarb --poly

# Run daemon (the real system)
python -m polyarb.daemon --host 0.0.0.0 --port 8080 --interval 10

# Docker (interactive — requires `run`, not `up`)
docker compose run --build polyarb --poly
```

## Architecture

Two layers: a **CLI playground** for manual exploration and the **daemon** which is the production system.

### Daemon (core system)
- **`polyarb/daemon/engine.py`** — Scan loop: fetches both platforms, matches markets, detects price deltas, pushes to Telegram/WebSocket.
- **`polyarb/daemon/server.py`** — Starlette REST API + WebSocket. Endpoints: `/status`, `/matches`, `/opportunities`, `/config`, `/execute/{id}`, `/ws`.
- **`polyarb/daemon/state.py`** — In-memory state: matches, opportunities, dedup tracking, WS client registry.

### Data Providers
- **`polyarb/data/async_live.py`** — AsyncLiveDataProvider (Polymarket Gamma API, no auth).
- **`polyarb/data/async_kalshi.py`** — AsyncKalshiDataProvider (Kalshi Trading API v2, no auth for reads).
- **`polyarb/data/live.py`** / **`mock.py`** — Sync providers for CLI playground.

### Cross-Platform Matching
- **`polyarb/matching/matcher.py`** — Two paths: token-based fallback (Jaccard + containment + SequenceMatcher) or all-pairs generation for cross-encoder scoring. 1:1 best-match-per-Poly constraint.
- **`polyarb/matching/encoder_client.py`** — Async client to the cross-encoder scoring service.
- **`polyarb/matching/normalize.py`** — Text normalization, tokenization, year extraction for matching.
- **`encoder/app.py`** — FastAPI service running `cross-encoder/stsb-TinyBERT-L-4`. Normalizes STS-B scores to 0.0–1.0.

### Execution
- **`polyarb/execution/kalshi.py`** — RSA-PSS signed auth, order placement, partial-failure recovery (cancels placed legs on failure).
- **`polyarb/execution/async_kalshi.py`** — Async httpx version of Kalshi client.
- **`polyarb/execution/executor.py`** — MockExecutor (paper trades), LiveExecutor (Polymarket CLOB stub — not yet implemented).

### CLI Playground
- **`polyarb/cli.py`** — `cmd.Cmd` interactive shell. `fetch --market <name>`, `fetch --expiration <hours>`, `scan`, `execute`, `portfolio`.
- **`polyarb/engine/`** — Single-platform detection: `single.py` (YES+NO imbalances), `multi.py` (neg_risk events), `scanner.py` (continuous polling).

### Shared
- **`polyarb/models.py`** — Frozen dataclasses: `Token`, `Market`, `Event`, `Opportunity`, `Order`, `OrderSet`. Enums: `Side`, `Action`, `ArbType`.
- **`polyarb/config.py`** — Config dataclass. Key fields: `min_profit`, `max_prob`, `scan_interval`, `order_size`, `dedup_window`, `match_final_threshold`, `approval_timeout`, `digest_interval`.
- **`polyarb/data/base.py`** — `DataProvider` / `AsyncDataProvider` protocols, `group_events()` helper.

## Environment Variables

- `KALSHI_API_KEY`, `KALSHI_KEY_FILE` — Kalshi authenticated execution
- `ENCODER_URL` — Cross-encoder service endpoint (e.g. `http://encoder:8000`)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_WEBHOOK_URL` — Telegram alerts

## Key Design Decisions

- **Protocol-based**: `DataProvider`, `AsyncDataProvider`, `Executor` use `typing.Protocol` — no ABC inheritance.
- **Immutable models**: All domain objects are frozen dataclasses.
- **Async daemon, sync CLI**: Daemon uses `asyncio` + `httpx`. CLI uses `urllib.request` for simplicity.
- **Two-stage matching**: Cheap token overlap for filtering or candidate generation, expensive cross-encoder for verification. Falls back to token-only when encoder is unavailable.
- **Python ≥3.11 required** (3.13 in Docker).

## Validation Sequence

The system must answer four questions in order. Each gates the next — if any answer is negative, stop and reassess before continuing.

1. **Record** — Persist every scan cycle's matched pairs with prices from both platforms to SQLite. Hook into `daemon/engine.py` `run_scan_once()`. Schema: pair IDs, timestamps, bid/ask from both sides, match confidence, raw delta.
2. **Cost model** — Compute net profit per pair after Kalshi fees ($0.02/contract entry, $0.99 cap), Polymarket spread, and execution spread (buy at ask, sell at bid on each side). A 3¢ delta is not profitable if fees eat 4¢.
3. **Lifetime analysis** — From recorded data, compute per-pair: first_seen, last_seen, duration. Answer: "how long does a profitable delta persist?" If median lifetime < execution latency, the thesis fails.
4. **Backtest** — Replay recorded scans: simulate entering every fee-adjusted profitable pair, closing when delta disappears. Output: total P&L, hit rate, average profit/trade, max drawdown. This is the go/no-go for building Polymarket CLOB execution.

**Do NOT build Polymarket execution until the backtest shows positive P&L.**

## Tests

Tests in `polyarb/tests/`. Key modules: `test_single.py`, `test_multi.py`, `test_orders.py` (single-platform), `test_matching.py` (cross-platform matcher), `test_encoder_client.py`, `test_kalshi_exec.py`, `test_daemon_engine.py`. No shared fixtures — each test constructs its own data.

## File Inventory

When asked to count how many code files are in this repo:

1. Read `prompt/file-inventory.md` to get the current inventory.
2. Scan the repo for any files not already listed in the inventory.
3. Add any new files to the appropriate section in `prompt/file-inventory.md`.
4. Update the summary table totals at the top.
