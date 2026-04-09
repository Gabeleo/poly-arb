"""Entry point: python -m polyarb.analysis

Runs the full analysis pipeline over collected snapshot data:
  1. Load unique markets from snapshot DB
  2. Generate candidate pairs (cheap SequenceMatcher ranking)
  3. Filter with bi-encoder (sentence-transformers, local CPU)
  4. Lifetime analysis (arb window durations)
  5. Backtest (simulated P&L)

All parameters are exposed as CLI flags for repeatable runs.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from polyarb.analysis.backtest import format_report as bt_report
from polyarb.analysis.backtest import run_backtest
from polyarb.analysis.costs import FeeParams
from polyarb.analysis.lifetime import analyze_pairs, summary
from polyarb.analysis.lifetime import format_report as lt_report
from polyarb.matching.matcher import MatchedPair, generate_all_pairs
from polyarb.models import Market, Side, Token

# ── Market loading ────────────────────────────────────────────


def load_unique_markets(db_path: Path) -> tuple[list[Market], list[Market]]:
    """Load one representative row per unique market from the snapshot DB."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    poly_rows = conn.execute("""
        SELECT condition_id, question, event_slug,
               yes_bid, yes_ask, no_bid, no_ask,
               volume, volume_24h, end_date
        FROM polymarket_snapshots
        WHERE rowid IN (
            SELECT MAX(rowid) FROM polymarket_snapshots
            GROUP BY condition_id
        )
    """).fetchall()

    kalshi_rows = conn.execute("""
        SELECT ticker, question, event_ticker,
               yes_bid, yes_ask, no_bid, no_ask,
               volume, volume_24h, close_time
        FROM kalshi_snapshots
        WHERE rowid IN (
            SELECT MAX(rowid) FROM kalshi_snapshots
            GROUP BY ticker
        )
    """).fetchall()

    conn.close()

    poly_markets = [
        Market(
            condition_id=r["condition_id"],
            question=r["question"],
            yes_token=Token(
                token_id=r["condition_id"] + "_yes",
                side=Side.YES,
                midpoint=(r["yes_bid"] + r["yes_ask"]) / 2,
                best_bid=r["yes_bid"],
                best_ask=r["yes_ask"],
            ),
            no_token=Token(
                token_id=r["condition_id"] + "_no",
                side=Side.NO,
                midpoint=(r["no_bid"] + r["no_ask"]) / 2,
                best_bid=r["no_bid"],
                best_ask=r["no_ask"],
            ),
            event_slug=r["event_slug"],
            volume=r["volume"],
            volume_24h=r["volume_24h"],
            platform="polymarket",
        )
        for r in poly_rows
    ]

    kalshi_markets = [
        Market(
            condition_id=r["ticker"],
            question=r["question"],
            yes_token=Token(
                token_id=r["ticker"] + "_yes",
                side=Side.YES,
                midpoint=(r["yes_bid"] + r["yes_ask"]) / 2,
                best_bid=r["yes_bid"],
                best_ask=r["yes_ask"],
            ),
            no_token=Token(
                token_id=r["ticker"] + "_no",
                side=Side.NO,
                midpoint=(r["no_bid"] + r["no_ask"]) / 2,
                best_bid=r["no_bid"],
                best_ask=r["no_ask"],
            ),
            event_slug=r["event_ticker"],
            volume=r["volume"],
            volume_24h=r["volume_24h"],
            platform="kalshi",
        )
        for r in kalshi_rows
    ]

    return poly_markets, kalshi_markets


# ── Matching ──────────────────────────────────────────────────


def match_markets(
    poly_markets: list[Market],
    kalshi_markets: list[Market],
    candidate_limit: int,
    bi_threshold: float,
    bi_max_keep: int,
) -> list[MatchedPair]:
    """Generate candidates then filter with bi-encoder."""
    from polyarb.matching.biencoder import BiEncoderFilter

    print(f"Generating candidate pairs (top {candidate_limit} by SequenceMatcher)...")
    t0 = time.perf_counter()
    candidates = generate_all_pairs(
        poly_markets,
        kalshi_markets,
        max_candidates=candidate_limit,
    )
    print(f"  {len(candidates)} candidates in {time.perf_counter() - t0:.1f}s")

    print("Loading bi-encoder (all-MiniLM-L6-v2)...")
    t0 = time.perf_counter()
    biencoder = BiEncoderFilter()
    print(f"  Model loaded in {time.perf_counter() - t0:.1f}s")

    print(f"Filtering (threshold={bi_threshold}, max_keep={bi_max_keep})...")
    t0 = time.perf_counter()
    matches = biencoder.filter_candidates(
        candidates,
        threshold=bi_threshold,
        max_keep=bi_max_keep,
    )
    print(f"  {len(candidates)} → {len(matches)} in {time.perf_counter() - t0:.1f}s")

    return matches


# ── Output ────────────────────────────────────────────────────


def print_matches(matches: list[MatchedPair]) -> None:
    """Print the matched pairs table."""
    print()
    print("=" * 80)
    print("MATCHED PAIRS (bi-encoder)")
    print("=" * 80)
    print(f"{'#':>3s}  {'Score':>5s}  {'Poly Question':42s}  {'Kalshi Question'}")
    print("-" * 80)
    for i, mp in enumerate(matches, 1):
        pq = (
            (mp.poly_market.question[:40] + "..")
            if len(mp.poly_market.question) > 40
            else mp.poly_market.question
        )
        kq = (
            (mp.kalshi_market.question[:30] + "..")
            if len(mp.kalshi_market.question) > 30
            else mp.kalshi_market.question
        )
        print(f"{i:3d}  {mp.confidence:.3f}  {pq:42s}  {kq}")
    print("=" * 80)


def save_results(
    output: Path,
    matches: list[MatchedPair],
    lt_stats: dict,
    bt_result,
    params: dict,
) -> None:
    """Write structured JSON results for downstream use."""
    data = {
        "run_ts": datetime.now(UTC).isoformat(),
        "params": params,
        "matched_pairs": [
            {
                "poly_cid": mp.poly_market.condition_id,
                "kalshi_ticker": mp.kalshi_market.condition_id,
                "poly_question": mp.poly_market.question,
                "kalshi_question": mp.kalshi_market.question,
                "bi_score": round(mp.confidence, 4),
            }
            for mp in matches
        ],
        "lifetime_summary": lt_stats,
        "backtest_summary": {
            "n_trades": bt_result.n_trades,
            "total_profit": round(bt_result.total_profit, 6),
            "avg_profit": round(bt_result.avg_profit, 6),
            "max_capital_deployed": round(bt_result.max_capital_deployed, 6),
            "return_on_max_capital": round(bt_result.return_on_max_capital, 6),
            "max_drawdown": round(bt_result.max_drawdown, 6),
            "avg_days_to_settlement": round(bt_result.avg_days_to_settlement, 2),
        },
    }
    output.write_text(json.dumps(data, indent=2))
    print(f"\nResults saved to {output}")


# ── Main ──────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="polyarb.analysis",
        description="Run cross-platform matching + analysis over collected snapshots.",
    )
    parser.add_argument(
        "--db",
        type=str,
        default="snapshots.db",
        help="path to snapshot database (default: snapshots.db)",
    )
    parser.add_argument(
        "--candidates",
        type=int,
        default=500,
        help="max candidate pairs from SequenceMatcher stage (default: 500)",
    )
    parser.add_argument(
        "--bi-threshold",
        type=float,
        default=0.15,
        help="bi-encoder cosine similarity threshold (default: 0.15)",
    )
    parser.add_argument(
        "--bi-max-keep",
        type=int,
        default=50,
        help="max pairs to keep after bi-encoder filtering (default: 50)",
    )
    parser.add_argument(
        "--poly-fee-rate",
        type=float,
        default=0.05,
        help="Polymarket taker fee rate (default: 0.05)",
    )
    parser.add_argument(
        "--kalshi-fee-cap",
        type=float,
        default=0.02,
        help="Kalshi per-contract fee cap (default: 0.02)",
    )
    parser.add_argument(
        "--scan-interval",
        type=int,
        default=30,
        help="expected seconds between scans for window detection (default: 30)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="write JSON results to file (optional)",
    )
    parser.add_argument(
        "--match-only",
        action="store_true",
        help="only run matching — skip lifetime and backtest",
    )

    args = parser.parse_args(argv)

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        print(f"Error: database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    fees = FeeParams(
        poly_fee_rate=args.poly_fee_rate,
        kalshi_fee_cap=args.kalshi_fee_cap,
    )

    params = {
        "db": str(db_path),
        "candidates": args.candidates,
        "bi_threshold": args.bi_threshold,
        "bi_max_keep": args.bi_max_keep,
        "poly_fee_rate": fees.poly_fee_rate,
        "kalshi_fee_cap": fees.kalshi_fee_cap,
        "scan_interval": args.scan_interval,
    }

    # ── Step 1: Load ────────────────────────────────────────────
    print(f"Database: {db_path}\n")
    print("Loading unique markets...")
    t0 = time.perf_counter()
    poly_markets, kalshi_markets = load_unique_markets(db_path)
    print(f"  Polymarket: {len(poly_markets)} markets")
    print(f"  Kalshi:     {len(kalshi_markets)} markets")
    print(f"  {time.perf_counter() - t0:.1f}s\n")

    # ── Step 2: Match ───────────────────────────────────────────
    matches = match_markets(
        poly_markets,
        kalshi_markets,
        candidate_limit=args.candidates,
        bi_threshold=args.bi_threshold,
        bi_max_keep=args.bi_max_keep,
    )

    print_matches(matches)

    if not matches:
        print("\nNo matched pairs — nothing to analyze.")
        sys.exit(0)

    if args.match_only:
        if args.output:
            save_results(
                Path(args.output),
                matches,
                {},
                type(
                    "R",
                    (),
                    {
                        "n_trades": 0,
                        "total_profit": 0,
                        "avg_profit": 0,
                        "max_capital_deployed": 0,
                        "return_on_max_capital": 0,
                        "max_drawdown": 0,
                        "avg_days_to_settlement": 0,
                    },
                )(),
                params,
            )
        sys.exit(0)

    pairs = [(mp.poly_market.condition_id, mp.kalshi_market.condition_id) for mp in matches]

    # ── Step 3: Lifetime analysis ───────────────────────────────
    print(f"\nRunning lifetime analysis on {len(pairs)} pairs...")
    t0 = time.perf_counter()
    lifetimes = analyze_pairs(db_path, pairs, fees=fees, scan_interval=args.scan_interval)
    print(f"  {time.perf_counter() - t0:.1f}s\n")
    print(lt_report(lifetimes))

    # ── Step 4: Backtest ────────────────────────────────────────
    print(f"\nRunning backtest on {len(pairs)} pairs...")
    t0 = time.perf_counter()
    bt_result = run_backtest(db_path, pairs, fees=fees)
    print(f"  {time.perf_counter() - t0:.1f}s\n")
    print(bt_report(bt_result))

    # ── Save ────────────────────────────────────────────────────
    if args.output:
        save_results(Path(args.output), matches, summary(lifetimes), bt_result, params)


if __name__ == "__main__":
    main()
