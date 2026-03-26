# Async Daemon Rewrite — Design Spec

Covers roadmap steps 3 (drop stdlib constraint) and 4 (move to async). Replaces synchronous urllib-based architecture with an async daemon + thin CLI client.

## Decisions

| Choice | Decision | Rationale |
|--------|----------|-----------|
| Architecture | Daemon + thin CLI | Matches roadmap direction (persistent service for step 6). Avoids bolting async onto cmd.Cmd. |
| HTTP client | `httpx` + `websockets` | httpx for REST (clean API, sync+async), websockets for WS (focused, gold standard). |
| CLI-to-daemon | REST API + WS push | Standard, curl-debuggable, extensible to web UI later. |
| API server | `uvicorn` + `starlette` | Lightweight ASGI. Native WS support. FastAPI upgrade path if needed. |
| Data ingestion | Async REST polling | WS feeds deferred. Providers designed as atomic units so a WS feed is a localized swap later. |
| WS push scope | Opportunities only | Execution feedback stays synchronous via REST responses. |
| CLI framework | `cmd.Cmd` (stdlib) | CLI is thin — just formats API responses. No need for prompt_toolkit. |

## Dependencies

**New runtime deps (pyproject.toml):**

```
dependencies = [
    "httpx>=0.27",
    "websockets>=12.0",
    "uvicorn>=0.30",
    "starlette>=0.37",
]
```

- `certifi` removed — httpx bundles its own CA certs.
- `cryptography>=41.0` stays as optional `[trade]` extra.

No changes needed in the Dockerfile install step — it already runs `pip install -e ".[dev,trade]"`, which pulls all deps from pyproject.toml. Only the entrypoint and compose config change (see Docker section).

## Architecture

```
                        +--------------------------+
                        |     polyarb.daemon       |
                        |                          |
                        |  +--------------------+  |
                        |  |   Scan Loop        |  |
  Polymarket API  <---->|  | asyncio.gather()   |  |
  (httpx async)         |  | poll both platforms |  |
                        |  | detect arbs        |  |
  Kalshi API      <---->|  | dedup & store      |  |
  (httpx async)         |  +--------+-----------+  |
                        |           |               |
                        |           v               |
                        |  +--------------------+   |
                        |  |   State (in-mem)   |   |
                        |  | matches, opps,     |   |
                        |  | config, ws clients |   |
                        |  +--------+-----------+   |
                        |           |               |
                        |           v               |
                        |  +--------------------+   |
                        |  |  Starlette App     |   |
                        |  |  REST + /ws        |   |
                        |  +--------+-----------+   |
                        +-----------|---------------+
                                    |
                            127.0.0.1:8080
                                    |
                        +-----------|---------------+
                        |   polyarb.client          |
                        |                           |
                        |  cmd.Cmd REPL             |
                        |  httpx.Client (sync)      |
                        |  websockets listener      |
                        |  (background thread)      |
                        +---------------------------+
```

## Module Structure

### polyarb/data/ (async additions)

**`base.py`** — Add `AsyncDataProvider` protocol alongside existing sync `DataProvider`:

```python
class AsyncDataProvider(Protocol):
    async def get_active_markets(self) -> list[Market]: ...
    async def get_events(self) -> list[Event]: ...
    async def search_markets(self, query: str, limit: int = 5) -> list[Market]: ...
    async def close(self) -> None: ...
```

The `close()` method lets the daemon shut down httpx clients cleanly.

**`async_live.py`** — `AsyncLiveDataProvider`. Same Gamma API logic as `live.py`, but uses `httpx.AsyncClient`. Client created once in `__init__`, reused for connection pooling.

**`async_kalshi.py`** — `AsyncKalshiDataProvider`. Same Kalshi API logic as `kalshi.py`, but uses `httpx.AsyncClient`.

Both providers are atomic units — each owns its own HTTP client, handles its own parsing, and exposes the same protocol. Swapping one to use a websocket feed later means changing the internals of that one file.

Existing sync providers (`live.py`, `kalshi.py`, `mock.py`) stay untouched until the final cleanup step.

### polyarb/execution/ (async additions)

**`async_kalshi.py`** — Async version of `KalshiClient` using `httpx.AsyncClient`. The RSA-PSS signing logic from `KalshiAuth` stays unchanged (it's pure crypto, no I/O). Only the HTTP transport moves from urllib to httpx async. The daemon's `/execute/{id}` endpoint calls this async client.

The existing sync `execution/kalshi.py` stays until cleanup — the `--mock` mode and old CLI still reference it.

### polyarb/daemon/

**`engine.py`** — Async scan loop:

```python
async def run_scan_loop(state: State, poly: AsyncDataProvider, kalshi: AsyncDataProvider, config: Config):
    while True:
        poly_markets, kalshi_markets = await asyncio.gather(
            poly.get_active_markets(),
            kalshi.get_active_markets(),
        )
        matches = await asyncio.to_thread(find_matches, poly_markets, kalshi_markets)
        # single-platform detection
        single_opps = await asyncio.to_thread(detect_single, poly_markets + kalshi_markets, config)
        events = await asyncio.to_thread(group_events, poly_markets + kalshi_markets)
        multi_opps = await asyncio.to_thread(detect_multi, events, config)

        new_matches = state.update_matches(matches)
        new_opps = state.update_opportunities(single_opps + multi_opps)

        # Push new cross-platform arbs to connected WS clients
        for match in new_matches:
            await state.broadcast({"type": "new_opportunity", "data": match.to_dict()})

        await asyncio.sleep(config.scan_interval)
```

**`state.py`** — In-memory state:

```python
@dataclass
class State:
    matches: list[MatchedPair]          # current cross-platform matches
    opportunities: list[Opportunity]     # current single-platform opps
    ws_clients: set[WebSocket]          # connected WS clients
    seen: dict[str, float]             # dedup hashes -> timestamp
    scan_count: int
    started_at: datetime
    last_scan_at: datetime | None
    config: Config
```

Methods: `update_matches()`, `update_opportunities()` (both handle dedup and return only new items), `broadcast()` (send to all WS clients, remove disconnected ones).

**`server.py`** — Starlette app:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/status` | Uptime, scan count, connected clients, last scan time |
| `GET` | `/matches` | Current cross-platform matched pairs with arb profits |
| `GET` | `/matches/{id}` | Detail for a specific match |
| `GET` | `/opportunities` | Current single-platform arb opportunities |
| `POST` | `/execute/{id}` | Execute Kalshi leg of a cross-platform arb. Returns order result or 409 if not connected. |
| `GET` | `/config` | Current config as JSON |
| `POST` | `/config` | Update config values |
| `WS` | `/ws` | Push channel for new opportunities |

All routes read from / write to the shared `State` instance. The Starlette app is created as a function that takes `State` and returns an `ASGIApp`.

API binds to `127.0.0.1:8080` by default. No auth — localhost only.

**`__main__.py`** — Entry point (`python -m polyarb.daemon`):

```
Args:
  --port PORT          API port (default: 8080)
  --interval SECONDS   Scan interval (default: 5.0)
  --host HOST          Bind address (default: 127.0.0.1)
```

Kalshi credentials read from env vars (same as current: `KALSHI_API_KEY`, `KALSHI_KEY_FILE`).

Wires up providers, state, engine, and server. Uses uvicorn's lifespan protocol to start/stop the scan loop alongside the HTTP server.

### polyarb/client/

**`api.py`** — Sync HTTP client wrapper:

```python
class DaemonClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8080"):
        self._client = httpx.Client(base_url=base_url, timeout=10.0)

    def get_status(self) -> dict: ...
    def get_matches(self) -> list[dict]: ...
    def get_match(self, id: int) -> dict: ...
    def get_opportunities(self) -> list[dict]: ...
    def execute(self, id: int) -> dict: ...
    def get_config(self) -> dict: ...
    def set_config(self, data: dict) -> dict: ...
```

**`ws_listener.py`** — Background thread that connects to `ws://127.0.0.1:{port}/ws` and prints alerts when new opportunities arrive. Reconnects on disconnect.

**`cli.py`** — `cmd.Cmd` subclass. Commands:

| Command | Action |
|---------|--------|
| `status` | `GET /status` |
| `cross` | `GET /matches` (replaces current fetch-both-then-match) |
| `opp <#>` | `GET /matches/{id}` |
| `execute <#>` | `POST /execute/{id}` |
| `config` | `GET /config` |
| `config key=val` | `POST /config` |
| `quit` | Exit |

The `fetch`, `scan`, `markets`, `expiring`, `detail` commands from the old CLI are no longer needed — the daemon continuously fetches and scans. `cross` replaces the manual fetch+match flow.

**`__main__.py`** — Entry point (`python -m polyarb.client` or `python -m polyarb`):

```
Args:
  --url URL    Daemon URL (default: http://127.0.0.1:8080)
```

### polyarb/__main__.py (updated)

The top-level entry point routes to the right mode:

```
python -m polyarb              -> starts client CLI (connects to daemon)
python -m polyarb --daemon     -> starts daemon
python -m polyarb --mock       -> old sync CLI with MockDataProvider (no daemon)
```

`--mock` preserves the old behavior for offline testing.

## Serialization

`MatchedPair`, `Opportunity`, `Market`, and other frozen dataclasses need JSON serialization for the REST API. Rather than adding a framework (Pydantic, marshmallow), add `to_dict()` methods to the model classes that return plain dicts. The daemon serializes with `json.dumps()`, the client deserializes with `json.loads()`.

This keeps the models framework-free. If FastAPI is adopted later, Pydantic models can wrap these dicts.

## Docker

**Dockerfile changes:**

- Entrypoint changes to run the daemon by default:
  ```dockerfile
  EXPOSE 8080
  ENTRYPOINT ["python", "-m", "polyarb", "--daemon"]
  ```

**compose.yaml changes:**

```yaml
services:
  daemon:
    build:
      context: .
      args:
        HTTP_PROXY: ${HTTP_PROXY:-}
        HTTPS_PROXY: ${HTTPS_PROXY:-}
        NO_PROXY: ${NO_PROXY:-}
        PROXY_CA_CERT_B64: ${PROXY_CA_CERT_B64:-}
    ports:
      - "8080:8080"
    command: ["--daemon", "--host", "0.0.0.0"]
    environment:
      - KALSHI_API_KEY=${KALSHI_API_KEY:-}
      - KALSHI_KEY_FILE=/run/secrets/kalshi_key
      - HTTP_PROXY=${HTTP_PROXY:-}
      - HTTPS_PROXY=${HTTPS_PROXY:-}
      - http_proxy=${http_proxy:-}
      - https_proxy=${https_proxy:-}
      - NO_PROXY=${NO_PROXY:-}
      - no_proxy=${no_proxy:-}
    volumes:
      - ${KALSHI_KEY_FILE:-.docker-dummy-key}:/run/secrets/kalshi_key:ro

  client:
    build:
      context: .
    stdin_open: true
    tty: true
    network_mode: "service:daemon"
    command: ["--url", "http://127.0.0.1:8080"]
    depends_on:
      - daemon
```

The daemon binds to `0.0.0.0` inside the container so the host can reach it on the published port. The client service shares the daemon's network namespace so it connects via localhost.

Alternatively, run the client on the host: `python -m polyarb --url http://localhost:8080`.

## Testing

New test files:

- `test_async_providers.py` — Test async data providers with mocked httpx responses (using `httpx.MockTransport`).
- `test_daemon_engine.py` — Test scan loop logic: concurrent fetch, dedup, state updates.
- `test_server.py` — Test REST endpoints using Starlette's `TestClient` (sync, no uvicorn needed).
- `test_client_api.py` — Test `DaemonClient` with mocked httpx responses.

Existing tests (`test_single.py`, `test_multi.py`, `test_orders.py`, `test_matching.py`, `test_kalshi.py`, `test_kalshi_exec.py`) are unaffected — they test sync logic that doesn't change.

## Implementation Steps (one commit each)

1. **Async data layer** — Add `AsyncDataProvider` protocol, `AsyncLiveDataProvider`, `AsyncKalshiDataProvider`. Add `AsyncKalshiClient` for execution. Update `pyproject.toml` deps. Tests for async providers.
2. **Daemon core** — `polyarb/daemon/` with engine, state, server. Scan loop, REST API, WS endpoint. Tests for engine and server.
3. **Thin CLI client** — `polyarb/client/` with api, ws_listener, cli. Updated `__main__.py` routing. Tests for client API.
4. **Docker** — Update Dockerfile entrypoint, rework compose.yaml for daemon+client services.
5. **Cleanup** — Remove old sync providers, scanner, old cli.py. Remove `certifi` from deps.
