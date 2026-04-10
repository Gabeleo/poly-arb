"""Prometheus metric definitions for the polyarb daemon."""

from prometheus_client import Counter, Gauge, Histogram

# Scan loop
scan_duration = Histogram(
    "polyarb_scan_duration_seconds",
    "Time spent in a single scan cycle",
)
scan_total = Counter(
    "polyarb_scan_total",
    "Total scan cycles",
    ["status"],
)

# Provider fetching
fetch_duration = Histogram(
    "polyarb_fetch_duration_seconds",
    "Time to fetch markets from a provider",
    ["provider"],
)
fetch_errors = Counter(
    "polyarb_fetch_errors_total",
    "Provider fetch failures",
    ["provider"],
)
markets_fetched = Gauge(
    "polyarb_markets_fetched",
    "Number of markets returned by last fetch",
    ["provider"],
)

# Matching
matches_found = Gauge(
    "polyarb_matches_found",
    "Number of cross-platform matches in latest scan",
)
opportunities_found = Gauge(
    "polyarb_opportunities_found",
    "Number of single-platform opportunities in latest scan",
)

# Encoder
encoder_duration = Histogram(
    "polyarb_encoder_duration_seconds",
    "Time spent scoring pairs via cross-encoder",
)
encoder_errors = Counter(
    "polyarb_encoder_errors_total",
    "Cross-encoder scoring failures",
)

# Circuit breaker
circuit_breaker_state = Gauge(
    "polyarb_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open)",
    ["provider"],
)

# WebSocket
ws_clients = Gauge(
    "polyarb_ws_clients_connected",
    "Active WebSocket connections",
)

# HTTP (instrumented via middleware)
http_request_duration = Histogram(
    "polyarb_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "route", "status"],
)

# Execution
execution_total = Counter(
    "polyarb_execution_total",
    "Trade execution outcomes",
    ["status"],
)
orphaned_legs = Gauge(
    "polyarb_orphaned_legs",
    "Number of execution legs in orphaned/sent state",
)

# Positions
position_value_dollars = Gauge(
    "polyarb_position_value_dollars",
    "Capital deployed (quantity * avg_price) by platform",
    ["platform"],
)

# Database
db_size_bytes = Gauge(
    "polyarb_db_size_bytes",
    "SQLite database file size in bytes",
    ["database"],
)
