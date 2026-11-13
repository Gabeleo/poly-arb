# poly-arb

Cross-platform prediction-market arbitrage scanner for **Polymarket** and **Kalshi**.

poly-arb asks one question: *do arbitrage opportunities between Polymarket and Kalshi exist, and do they last long enough to act on profitably?* It pulls public market data from both platforms, matches equivalent markets across them, detects mispricings, and runs a validation pipeline (record → cost → lifetime → backtest) before any money is risked. Live order execution stays blocked until a backtest shows positive P&L after fees.

**Who it's for:** I built it for my own research into prediction-market pricing. It's also useful to anyone curious whether these cross-venue spreads survive fees and latency.

## Features

- **Single-market detection:** YES + NO prices that sum to more or less than $1.
- **Multi-outcome detection:** mispriced `neg_risk` events whose outcome prices don't sum to 1.
- **Cross-platform matching:** text normalization and token matching, plus an optional cross-encoder service (a pretrained `stsb-TinyBERT-L-4` model) that scores candidate market pairs.
- **Daemon:** a scan loop with a circuit breaker and fetch timeouts, a REST + WebSocket API (API-key protected), and Prometheus metrics.
- **Interactive CLI client** that connects to the daemon.
- **Recorder:** snapshots both platforms to SQLite every 30 s for offline analysis.
- **Analysis pipeline:** fee models (Polymarket taker fees, Kalshi entry fees), arbitrage-window lifetime statistics, and a P&L backtester.
- **Alerts:** Telegram notifications with a fee-aware approval flow.
- **Observability:** Prometheus and Grafana dashboards with alerting rules.

## Built with

Python 3.11+, httpx, Starlette/Uvicorn, websockets, Pydantic, SQLAlchemy + Alembic (SQLite or Postgres), prometheus-client, and sentence-transformers + FastAPI for the encoder service. Docker Compose runs the full stack. CI uses GitHub Actions (tests, lint, build), with ruff, mypy, pytest + hypothesis, and pre-commit with detect-secrets.

## Quick start

Requires Python 3.11+. Market data comes from public endpoints, so no API keys are needed to try it.

```bash
git clone https://github.com/Gabeleo/poly-arb.git
cd poly-arb
python -m venv .venv && source .venv/bin/activate   # or: uv venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run detection on synthetic mock markets
python -m polyarb --mock

# Run the test suite
pytest
```

## Usage

```bash
# Start the daemon (REST + WebSocket + scan loop) on 127.0.0.1:8080
python -m polyarb --daemon --interval 5

# Connect the interactive client to a running daemon
python -m polyarb --url http://127.0.0.1:8080

# Record live snapshots from both platforms to SQLite
python -m polyarb.recorder --db snapshots.db --interval 30

# Match markets and run cost / lifetime / backtest analysis on recorded data
python -m polyarb.analysis --db snapshots.db
```

### Full stack with Docker

```bash
cp .env.example .env    # fill in only what you need; everything is optional
make build
make run                # daemon :8080, encoder :8000, Prometheus :9090, Grafana :3000
```

### Configuration

All settings are environment variables with the `POLYARB_` prefix. See [`.env.example`](.env.example). Never commit a real `.env` or key file: both are already in `.gitignore`.

## Development

```bash
make test        # pytest
make lint        # ruff check + format check
make typecheck   # mypy
make fmt         # auto-format
make migrate     # alembic upgrade head
```

See [`docs/CODEBASE.md`](docs/CODEBASE.md) for a module-by-module tour of the codebase.

## Roadmap

- [ ] Run the recorder long enough to build a multi-week snapshot dataset
- [ ] Finish the lifetime analysis: how long do cross-platform arbitrage windows actually stay open?
- [ ] Backtest with realistic fees, slippage, and capital limits; decide go/no-go on execution
- [ ] Improve cross-platform matching precision (fewer false pairs on similarly worded markets)
- [ ] Complete the `LiveExecutor` (only if the backtest shows positive P&L)
- [ ] Expand the Grafana dashboards and alerting

## Disclaimer

This is a research project, not financial advice. Prediction-market trading carries real financial risk and may be restricted in your jurisdiction.

## License

[MIT](LICENSE) © 2026 Gabriel Leo
