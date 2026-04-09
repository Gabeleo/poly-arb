"""Generate a 7-day mock snapshot database for analysis testing.

Creates realistic time-series data for 50 Polymarket + 25 Kalshi markets
across 20,160 scans (30s intervals, 03/01/2026 - 03/08/2026).

Market composition:
  - 10 cross-platform matched pairs (same underlying event)
  - 5 pairs with profitable arbs that appear/disappear
  - 3 pairs with small deltas that fees eat (false positives)
  - 2 stable matched pairs (no arb)
  - 30 unmatched Polymarket-only markets (noise)
  - 5 unmatched Kalshi-only markets (noise)

Usage:
    python -m polyarb.tests.generate_mock_db [--output mock_snapshots.db]
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

# Import schema from the real module
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from polyarb.recorder.db import SCHEMA

# ── Configuration ─────────────────────────────────────────────

START_TS = "2026-03-01T00:00:00Z"
END_TS = "2026-03-08T00:00:00Z"
INTERVAL = 30  # seconds
TOTAL_SCANS = 7 * 24 * 60 * 60 // INTERVAL  # 20,160

SEED = 42
BATCH_SIZE = 5000  # rows per executemany for performance


# ── Market definitions ────────────────────────────────────────


@dataclass
class MarketDef:
    """Template for a mock market."""

    id: str
    question: str
    event_slug: str
    base_price: float  # YES midpoint around which prices oscillate
    spread: float  # half-spread (bid = mid - spread, ask = mid + spread)
    volume: float
    volume_24h_base: float
    end_date: str
    platform: str  # "polymarket" or "kalshi"
    # Price behavior
    drift_per_day: float = 0.0  # linear trend
    volatility: float = 0.005  # random walk step size
    mean_revert_strength: float = 0.02  # pull back toward base


@dataclass
class MatchedPairDef:
    """A cross-platform pair with controlled delta behavior."""

    poly: MarketDef
    kalshi: MarketDef
    arb_type: str  # "profitable", "false_positive", "stable"
    # Arb windows: list of (start_scan, end_scan, delta_magnitude)
    arb_windows: list[tuple[int, int, float]]


def _make_poly(
    idx: int,
    question: str,
    slug: str,
    base_price: float,
    volume: float = 500_000,
    end: str = "2026-06-30T00:00:00+00:00",
    **kwargs,
) -> MarketDef:
    cid = f"0x{idx:04d}{'a' * 36}"[:42]
    return MarketDef(
        id=cid,
        question=question,
        event_slug=slug,
        base_price=base_price,
        spread=0.01,
        volume=volume,
        volume_24h_base=volume * 0.03,
        end_date=end,
        platform="polymarket",
        **kwargs,
    )


def _make_kalshi(
    idx: int,
    question: str,
    event_ticker: str,
    base_price: float,
    volume: float = 30_000,
    end: str = "2026-06-30T00:00:00+00:00",
    **kwargs,
) -> MarketDef:
    ticker = f"KX{event_ticker}-{idx:03d}"
    return MarketDef(
        id=ticker,
        question=question,
        event_slug=event_ticker,
        base_price=base_price,
        spread=0.02,
        volume=volume,
        volume_24h_base=volume * 0.05,
        end_date=end,
        platform="kalshi",
        **kwargs,
    )


def build_market_universe() -> tuple[list[MatchedPairDef], list[MarketDef], list[MarketDef]]:  # noqa: C901
    """Define all markets and their behaviors."""
    matched_pairs: list[MatchedPairDef] = []

    # ── 5 profitable arb pairs ────────────────────────────────
    # These have windows where delta > ~4¢ (enough to survive fees)
    profitable_specs = [
        (
            "Will Bitcoin exceed $150,000 by June 30?",
            "Bitcoin above $150,000?",
            "will-bitcoin-exceed-150000-by-june-30",
            "BTC150K",
            0.42,
            0.40,
            1_200_000,
            50_000,
        ),
        (
            "Will ETH hit $10,000 by end of 2026?",
            "Ethereum above $10,000?",
            "will-eth-hit-10000-by-end-of-2026",
            "ETH10K",
            0.28,
            0.25,
            800_000,
            35_000,
        ),
        (
            "Will the Fed cut rates in March 2026?",
            "Fed rate cut in March 2026?",
            "will-the-fed-cut-rates-in-march-2026",
            "FEDMAR26",
            0.65,
            0.62,
            3_000_000,
            80_000,
        ),
        (
            "Will Trump win 2028 Republican nomination?",
            "Trump 2028 GOP nominee?",
            "will-trump-win-2028-republican-nomination",
            "TRUMP28",
            0.55,
            0.52,
            5_000_000,
            120_000,
        ),
        (
            "Will SpaceX Starship reach orbit by April 2026?",
            "SpaceX Starship orbital by April 2026?",
            "will-spacex-starship-reach-orbit-by-april-2026",
            "SPACEX26",
            0.70,
            0.67,
            600_000,
            25_000,
        ),
    ]

    for i, (pq, kq, slug, kticker, p_price, k_price, pvol, kvol) in enumerate(profitable_specs):
        # Each profitable pair gets 2-4 arb windows scattered across the 7 days
        # Windows last 2-8 hours with delta 4-8¢
        rng = random.Random(SEED + i)
        windows = []
        n_windows = rng.randint(2, 4)
        for _w in range(n_windows):
            day = rng.randint(0, 6)
            hour = rng.randint(0, 20)
            start_scan = (day * 24 + hour) * 120  # scans per hour = 120
            duration_hours = rng.uniform(2, 8)
            end_scan = min(start_scan + int(duration_hours * 120), TOTAL_SCANS - 1)
            delta = rng.uniform(0.04, 0.08)
            windows.append((start_scan, end_scan, delta))
        # Sort and remove overlaps
        windows.sort()
        clean = [windows[0]]
        for w in windows[1:]:
            if w[0] > clean[-1][1] + 120:  # gap of at least 1 hour
                clean.append(w)
        matched_pairs.append(
            MatchedPairDef(
                poly=_make_poly(i, pq, slug, p_price, volume=pvol),
                kalshi=_make_kalshi(i, kq, kticker, k_price, volume=kvol),
                arb_type="profitable",
                arb_windows=clean,
            )
        )

    # ── 3 false positive pairs (small delta, fees eat it) ─────
    false_pos_specs = [
        (
            "Will GTA VI release in 2026?",
            "GTA VI 2026 release?",
            "will-gta-vi-release-in-2026",
            "GTA6",
            0.45,
            0.44,
            400_000,
            20_000,
        ),
        (
            "Will US GDP growth exceed 3% in Q2 2026?",
            "US GDP above 3% Q2 2026?",
            "will-us-gdp-growth-exceed-3-percent-q2-2026",
            "GDPQ2",
            0.35,
            0.34,
            250_000,
            15_000,
        ),
        (
            "Will AI pass the Turing test by 2027?",
            "AI Turing test by 2027?",
            "will-ai-pass-turing-test-by-2027",
            "AITURING",
            0.20,
            0.19,
            300_000,
            18_000,
        ),
    ]

    for i, (pq, kq, slug, kticker, p_price, k_price, pvol, kvol) in enumerate(false_pos_specs):
        rng = random.Random(SEED + 100 + i)
        windows = []
        for _w in range(3):
            day = rng.randint(0, 6)
            hour = rng.randint(0, 20)
            start_scan = (day * 24 + hour) * 120
            duration_hours = rng.uniform(1, 4)
            end_scan = min(start_scan + int(duration_hours * 120), TOTAL_SCANS - 1)
            delta = rng.uniform(0.015, 0.025)  # 1.5-2.5¢, below fee threshold
            windows.append((start_scan, end_scan, delta))
        windows.sort()
        clean = [windows[0]]
        for w in windows[1:]:
            if w[0] > clean[-1][1] + 120:
                clean.append(w)
        matched_pairs.append(
            MatchedPairDef(
                poly=_make_poly(5 + i, pq, slug, p_price, volume=pvol),
                kalshi=_make_kalshi(5 + i, kq, kticker, k_price, volume=kvol),
                arb_type="false_positive",
                arb_windows=clean,
            )
        )

    # ── 2 stable matched pairs (no arb) ──────────────────────
    stable_specs = [
        (
            "Will there be a US government shutdown in 2026?",
            "US government shutdown 2026?",
            "will-there-be-us-government-shutdown-2026",
            "GOVSHUT26",
            0.30,
            0.30,
            1_500_000,
            40_000,
        ),
        (
            "Will the S&P 500 close above 6000 by June 2026?",
            "S&P 500 above 6000 by June 2026?",
            "will-sp-500-close-above-6000-by-june-2026",
            "SP6000",
            0.60,
            0.60,
            2_000_000,
            60_000,
        ),
    ]

    for i, (pq, kq, slug, kticker, p_price, k_price, pvol, kvol) in enumerate(stable_specs):
        matched_pairs.append(
            MatchedPairDef(
                poly=_make_poly(8 + i, pq, slug, p_price, volume=pvol),
                kalshi=_make_kalshi(8 + i, kq, kticker, k_price, volume=kvol),
                arb_type="stable",
                arb_windows=[],
            )
        )

    # ── 30 unmatched Polymarket markets (noise) ──────────────
    noise_poly: list[MarketDef] = []
    noise_topics = [
        "Super Bowl LXII winner",
        "Oscar Best Picture 2027",
        "Next UK Prime Minister",
        "Mars mission by 2030",
        "NYC mayor 2029",
        "California earthquake M7+ 2026",
        "Taylor Swift album 2026",
        "Tesla stock above $500",
        "Netflix subscribers 300M",
        "Dogecoin above $1",
        "US inflation below 2%",
        "Amazon stock split 2026",
        "World Cup 2026 winner",
        "Next Supreme Court vacancy",
        "TikTok banned in US",
        "Apple car announced",
        "Nuclear fusion breakeven 2027",
        "UFO disclosure 2026",
        "Olympics 2028 boycott",
        "Minimum wage $20 federal",
        "Student loan forgiveness",
        "Bitcoin ETF $100B AUM",
        "Lab-grown meat FDA approved",
        "Self-driving taxi nationwide",
        "Twitter/X profitable 2026",
        "Disney+ subscriber loss",
        "ChatGPT-5 released 2026",
        "Hyperloop operational",
        "Commercial space tourism 1000 passengers",
        "Quantum supremacy 2027",
    ]
    for i, topic in enumerate(noise_topics):
        rng = random.Random(SEED + 200 + i)
        base_p = rng.uniform(0.10, 0.90)
        vol = rng.uniform(15_000, 4_000_000)
        noise_poly.append(
            _make_poly(
                10 + i,
                f"Will {topic}?",
                topic.lower().replace(" ", "-").replace("/", "-"),
                base_price=round(base_p, 2),
                volume=round(vol, -3),
                volatility=rng.uniform(0.002, 0.010),
                drift_per_day=rng.uniform(-0.005, 0.005),
            )
        )

    # ─�� 5 unmatched Kalshi markets (noise) ────────────────────
    noise_kalshi: list[MarketDef] = []
    kalshi_noise_topics = [
        ("RAIN-NYC-26MAR15", "Rain in NYC on March 15?", "RAIN-NYC"),
        ("TEMP-CHI-26MAR20", "Chicago temp above 60F on March 20?", "TEMP-CHI"),
        ("JOBS-26MAR", "March 2026 jobs report above 200k?", "JOBS"),
        ("CPI-26APR", "April 2026 CPI above 3%?", "CPI"),
        ("GOLD-26Q2", "Gold above $3000 Q2 2026?", "GOLD"),
    ]
    for i, (_ticker, question, evt) in enumerate(kalshi_noise_topics):
        rng = random.Random(SEED + 300 + i)
        base_p = rng.uniform(0.15, 0.85)
        vol = rng.uniform(10_000, 80_000)
        noise_kalshi.append(
            _make_kalshi(
                10 + i,
                question,
                evt,
                base_price=round(base_p, 2),
                volume=round(vol, -3),
                volatility=rng.uniform(0.003, 0.012),
                drift_per_day=rng.uniform(-0.005, 0.005),
            )
        )

    return matched_pairs, noise_poly, noise_kalshi


# ── Price simulation ──────────────────────────────────────────


def simulate_price_solo(
    mkt: MarketDef,
    rng: random.Random,
    price_state: dict[str, float],
) -> tuple[float, float, float, float]:
    """Simulate bid/ask for an UNMATCHED market (independent random walk).

    Returns (yes_bid, yes_ask, no_bid, no_ask).
    """
    key = mkt.id
    if key not in price_state:
        price_state[key] = mkt.base_price

    current = price_state[key]

    shock = rng.gauss(0, mkt.volatility)
    drift = mkt.drift_per_day / (24 * 120)
    revert = mkt.mean_revert_strength * (mkt.base_price - current)
    new_price = max(0.03, min(0.97, current + shock + drift + revert))
    price_state[key] = new_price

    yes_mid = round(new_price, 4)
    half = mkt.spread

    if mkt.platform == "polymarket":
        yes_bid = round(max(0.01, yes_mid - half), 4)
        yes_ask = round(min(0.99, yes_mid + half), 4)
        no_bid = round(1.0 - yes_ask, 4)
        no_ask = round(1.0 - yes_bid, 4)
    else:
        yes_bid = round(max(0.01, yes_mid - half), 4)
        yes_ask = round(min(0.99, yes_mid + half), 4)
        no_mid = round(1.0 - yes_mid, 4)
        no_bid = round(max(0.01, no_mid - half), 4)
        no_ask = round(min(0.99, no_mid + half), 4)

    return yes_bid, yes_ask, no_bid, no_ask


def simulate_matched_pair(
    pair: MatchedPairDef,
    scan_idx: int,
    rng: random.Random,
    price_state: dict[str, float],
) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float]]:
    """Simulate correlated prices for a matched pair.

    One shared "true price" random walk drives both platforms.
    Each platform adds small independent noise (~0.5¢).
    During arb windows, an explicit delta is injected.

    Returns (poly_prices, kalshi_prices) as (yes_bid, yes_ask, no_bid, no_ask).
    """
    shared_key = f"_shared_{pair.poly.id}"
    if shared_key not in price_state:
        # Average of both base prices as shared starting point
        price_state[shared_key] = (pair.poly.base_price + pair.kalshi.base_price) / 2.0

    current = price_state[shared_key]

    # Shared random walk (one "true" underlying price)
    shock = rng.gauss(0, pair.poly.volatility)
    drift = pair.poly.drift_per_day / (24 * 120)
    base = (pair.poly.base_price + pair.kalshi.base_price) / 2.0
    revert = pair.poly.mean_revert_strength * (base - current)
    true_price = max(0.05, min(0.95, current + shock + drift + revert))
    price_state[shared_key] = true_price

    # Small independent platform noise (±0.5¢ typical)
    poly_mid = max(0.03, min(0.97, true_price + rng.gauss(0, 0.003)))
    kalshi_mid = max(0.03, min(0.97, true_price + rng.gauss(0, 0.003)))

    # Check if inside an arb window
    arb_delta = 0.0
    arb_direction = 0  # 0 = none, 1 = poly cheap, -1 = kalshi cheap
    for w_start, w_end, w_delta in pair.arb_windows:
        if w_start <= scan_idx <= w_end:
            arb_delta = w_delta
            # Consistent direction within a window (seeded by window start)
            arb_direction = 1 if (w_start % 2 == 0) else -1
            # Ramp in/out over ~5 minutes (10 scans) at window edges
            edge_scans = 10
            if scan_idx < w_start + edge_scans:
                arb_delta *= (scan_idx - w_start) / edge_scans
            elif scan_idx > w_end - edge_scans:
                arb_delta *= (w_end - scan_idx) / edge_scans
            break

    # Apply arb delta: shift platforms in opposite directions
    if arb_direction != 0:
        half_d = arb_delta / 2.0
        poly_mid = max(0.03, min(0.97, poly_mid - arb_direction * half_d))
        kalshi_mid = max(0.03, min(0.97, kalshi_mid + arb_direction * half_d))

    # Build bid/ask from midpoints
    p_half = pair.poly.spread
    py_bid = round(max(0.01, poly_mid - p_half), 4)
    py_ask = round(min(0.99, poly_mid + p_half), 4)
    pn_bid = round(1.0 - py_ask, 4)
    pn_ask = round(1.0 - py_bid, 4)

    k_half = pair.kalshi.spread
    ky_bid = round(max(0.01, kalshi_mid - k_half), 4)
    ky_ask = round(min(0.99, kalshi_mid + k_half), 4)
    kn_mid = round(1.0 - kalshi_mid, 4)
    kn_bid = round(max(0.01, kn_mid - k_half), 4)
    kn_ask = round(min(0.99, kn_mid + k_half), 4)

    return (py_bid, py_ask, pn_bid, pn_ask), (ky_bid, ky_ask, kn_bid, kn_ask)


# ── Row generation ────────────────────────────────────────────


def scan_ts_at(scan_idx: int) -> str:
    """ISO timestamp for the given scan index (0-based from START_TS)."""
    # 2026-03-01T00:00:00Z + scan_idx * 30 seconds
    total_seconds = scan_idx * INTERVAL
    days = total_seconds // 86400
    remainder = total_seconds % 86400
    hours = remainder // 3600
    minutes = (remainder % 3600) // 60
    seconds = remainder % 60
    day = 1 + days
    return f"2026-03-{day:02d}T{hours:02d}:{minutes:02d}:{seconds:02d}Z"


def generate_rows(
    matched_pairs: list[MatchedPairDef],
    noise_poly: list[MarketDef],
    noise_kalshi: list[MarketDef],
) -> tuple[list[tuple], list[tuple]]:
    """Generate all rows for both tables."""
    rng = random.Random(SEED)
    price_state: dict[str, float] = {}

    poly_rows: list[tuple] = []
    kalshi_rows: list[tuple] = []

    for scan_idx in range(TOTAL_SCANS):
        ts = scan_ts_at(scan_idx)

        # ── Matched pairs (correlated prices) ─────────────────
        for pair in matched_pairs:
            p_prices, k_prices = simulate_matched_pair(
                pair,
                scan_idx,
                rng,
                price_state,
            )

            # Volume jitter (+-20%)
            p_vol24 = round(pair.poly.volume_24h_base * rng.uniform(0.8, 1.2), 2)
            k_vol24 = round(pair.kalshi.volume_24h_base * rng.uniform(0.8, 1.2), 2)

            poly_rows.append(
                (
                    ts,
                    pair.poly.id,
                    pair.poly.question,
                    pair.poly.event_slug,
                    p_prices[0],
                    p_prices[1],
                    p_prices[2],
                    p_prices[3],
                    pair.poly.volume,
                    p_vol24,
                    pair.poly.end_date,
                )
            )
            kalshi_rows.append(
                (
                    ts,
                    pair.kalshi.id,
                    pair.kalshi.question,
                    pair.kalshi.event_slug,
                    k_prices[0],
                    k_prices[1],
                    k_prices[2],
                    k_prices[3],
                    pair.kalshi.volume,
                    k_vol24,
                    pair.kalshi.end_date,
                )
            )

        # ── Noise poly (independent prices) ───────────────────
        for mkt in noise_poly:
            prices = simulate_price_solo(mkt, rng, price_state)
            vol24 = round(mkt.volume_24h_base * rng.uniform(0.8, 1.2), 2)
            poly_rows.append(
                (
                    ts,
                    mkt.id,
                    mkt.question,
                    mkt.event_slug,
                    prices[0],
                    prices[1],
                    prices[2],
                    prices[3],
                    mkt.volume,
                    vol24,
                    mkt.end_date,
                )
            )

        # ── Noise kalshi ─────────���─────────────────────────���──
        for mkt in noise_kalshi:
            prices = simulate_price_solo(mkt, rng, price_state)
            vol24 = round(mkt.volume_24h_base * rng.uniform(0.8, 1.2), 2)
            kalshi_rows.append(
                (
                    ts,
                    mkt.id,
                    mkt.question,
                    mkt.event_slug,
                    prices[0],
                    prices[1],
                    prices[2],
                    prices[3],
                    mkt.volume,
                    vol24,
                    mkt.end_date,
                )
            )

        # Progress
        if scan_idx % 2880 == 0:  # every ~day
            day_num = scan_idx // 2880 + 1
            print(f"  day {day_num}/7 — {len(poly_rows):,} poly, {len(kalshi_rows):,} kalshi rows")

    return poly_rows, kalshi_rows


# ── Database writing ────────���─────────────────────────────────


def write_db(path: Path, poly_rows: list[tuple], kalshi_rows: list[tuple]) -> None:
    """Write rows to SQLite using the real schema."""
    if path.exists():
        path.unlink()

    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)

    # Batch insert for performance
    poly_sql = """INSERT INTO polymarket_snapshots
        (scan_ts, condition_id, question, event_slug,
         yes_bid, yes_ask, no_bid, no_ask,
         volume, volume_24h, end_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

    kalshi_sql = """INSERT INTO kalshi_snapshots
        (scan_ts, ticker, question, event_ticker,
         yes_bid, yes_ask, no_bid, no_ask,
         volume, volume_24h, close_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

    print(f"  writing {len(poly_rows):,} polymarket rows...")
    for i in range(0, len(poly_rows), BATCH_SIZE):
        conn.executemany(poly_sql, poly_rows[i : i + BATCH_SIZE])
    conn.commit()

    print(f"  writing {len(kalshi_rows):,} kalshi rows...")
    for i in range(0, len(kalshi_rows), BATCH_SIZE):
        conn.executemany(kalshi_sql, kalshi_rows[i : i + BATCH_SIZE])
    conn.commit()

    conn.close()


# ── Main ──────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate mock snapshot database")
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="mock_snapshots.db",
        help="output database path (default: mock_snapshots.db)",
    )
    args = parser.parse_args()

    output_path = Path(args.output)

    print("Building market universe...")
    matched_pairs, noise_poly, noise_kalshi = build_market_universe()
    n_poly = len(matched_pairs) + len(noise_poly)  # 10 matched + 30 noise = 40
    n_kalshi = len(matched_pairs) + len(noise_kalshi)  # 10 matched + 5 noise = 15
    print(f"  {n_poly} poly markets, {n_kalshi} kalshi markets")
    print(
        f"  {len(matched_pairs)} matched pairs ({sum(1 for p in matched_pairs if p.arb_type == 'profitable')} profitable, "
        f"{sum(1 for p in matched_pairs if p.arb_type == 'false_positive')} false positive, "
        f"{sum(1 for p in matched_pairs if p.arb_type == 'stable')} stable)"
    )
    print(f"  {TOTAL_SCANS:,} scans over 7 days at {INTERVAL}s intervals")

    print("\nGenerating rows...")
    poly_rows, kalshi_rows = generate_rows(matched_pairs, noise_poly, noise_kalshi)

    print(f"\nWriting to {output_path}...")
    write_db(output_path, poly_rows, kalshi_rows)

    # ── Verification ──────────────────────────────────────────
    conn = sqlite3.connect(str(output_path))

    poly_count = conn.execute("SELECT COUNT(*) FROM polymarket_snapshots").fetchone()[0]
    kalshi_count = conn.execute("SELECT COUNT(*) FROM kalshi_snapshots").fetchone()[0]
    poly_scans = conn.execute(
        "SELECT COUNT(DISTINCT scan_ts) FROM polymarket_snapshots"
    ).fetchone()[0]
    kalshi_scans = conn.execute("SELECT COUNT(DISTINCT scan_ts) FROM kalshi_snapshots").fetchone()[
        0
    ]
    poly_markets = conn.execute(
        "SELECT COUNT(DISTINCT condition_id) FROM polymarket_snapshots"
    ).fetchone()[0]
    kalshi_markets = conn.execute("SELECT COUNT(DISTINCT ticker) FROM kalshi_snapshots").fetchone()[
        0
    ]

    first_ts = conn.execute("SELECT MIN(scan_ts) FROM polymarket_snapshots").fetchone()[0]
    last_ts = conn.execute("SELECT MAX(scan_ts) FROM polymarket_snapshots").fetchone()[0]

    size_bytes = output_path.stat().st_size
    size_mb = size_bytes / (1024 * 1024)

    conn.close()

    print(f"\n{'=' * 50}")
    print(f"Mock database: {output_path} ({size_mb:.1f} MB)")
    print(f"  Polymarket: {poly_count:,} rows, {poly_markets} markets, {poly_scans:,} scans")
    print(f"  Kalshi:     {kalshi_count:,} rows, {kalshi_markets} markets, {kalshi_scans:,} scans")
    print(f"  Time range: {first_ts} → {last_ts}")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
