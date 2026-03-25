# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Polyarb is a Polymarket arbitrage detection & semi-auto trading system. It monitors binary option markets for YES+NO price imbalances (single-market) and multi-outcome events (neg_risk markets) where the sum of prices deviates from $1.00. Zero external runtime dependencies — uses only Python stdlib

## Guidelines

I want you to self-verify your work by testing it end to end. DO not return control to me until you've met my requirements and it works as expected. 

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

# Run CLI (mock data)
python -m polyarb

# Run CLI (live Polymarket data)
python -m polyarb --poly

# Run CLI (live Kalshi data)
python -m polyarb --kalshi

# Docker (interactive — requires `run`, not `up`)
docker compose run --build polyarb --poly
```

## Architecture

The system follows a pipeline: **Data → Detection → Opportunity → Orders → Execution**.

- **`polyarb/models.py`** — Frozen dataclasses (`Token`, `Market`, `Event`, `Opportunity`, `Order`, `OrderSet`) and enums (`Side`, `Action`, `ArbType`) shared across all modules.
- **`polyarb/data/`** — `DataProvider` protocol with `LiveDataProvider` (Polymarket Gamma API, no auth) and `MockDataProvider` (synthetic markets with configurable drift).
- **`polyarb/engine/`** — Detection logic split by strategy:
  - `single.py`: Single-market YES+NO imbalances (underprice/overprice).
  - `multi.py`: Multi-outcome neg_risk event imbalances.
  - `scanner.py`: Continuous background scanner with hash-based deduplication.
- **`polyarb/execution/`** — `build_order_set()` translates Opportunities into OrderSets. `MockExecutor` paper-trades; `LiveExecutor` is a stub for future CLOB integration.
- **`polyarb/alerts/`** — `Alerter` protocol with `ConsoleAlerter` (ANSI-colored output).
- **`polyarb/cli.py`** — `cmd.Cmd`-based interactive shell (fetch, scan, execute, portfolio, config).
- **`polyarb/config.py`** — `Config` dataclass with defaults: `min_profit=0.005`, `max_prob=0.95`, `scan_interval=10.0`, `order_size=10.0`, `dedup_window=60`.

## Key Design Decisions

- **Protocol-based**: `DataProvider` and `Executor` use `typing.Protocol` for duck typing — no ABC inheritance.
- **Immutable models**: All domain objects are frozen dataclasses.
- **No external deps at runtime**: HTTP via `urllib.request`, JSON via stdlib. Only dev dep is `pytest>=7.0`.
- **Python ≥3.11 required** (3.13 in Docker).

## Tests

Tests live in `polyarb/tests/` and use mock market builders defined inline. Three modules: `test_single.py` (single-market detection), `test_multi.py` (multi-outcome events), `test_orders.py` (order building). No fixtures or conftest — each test constructs its own data.
