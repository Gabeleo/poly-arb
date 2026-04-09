"""Lifetime analysis for cross-platform arb opportunities.

Reads recorded snapshots, applies the cost model to each scan for
a set of matched pairs, identifies consecutive profitable windows,
and computes duration statistics.

Key question answered: "How long does a profitable delta persist?"
If median lifetime < execution latency, the thesis fails.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from polyarb.analysis.costs import ArbResult, FeeParams, compute_arb, is_profitable


@dataclass(frozen=True)
class ArbWindow:
    """A contiguous run of profitable scans for a matched pair."""

    first_seen: str
    last_seen: str
    duration_seconds: int
    n_scans: int
    peak_profit: float
    mean_profit: float
    direction: str


@dataclass
class PairLifetime:
    """Lifetime analysis results for a single matched pair."""

    poly_cid: str
    kalshi_ticker: str
    poly_question: str
    kalshi_question: str
    total_scans: int
    profitable_scans: int
    windows: list[ArbWindow] = field(default_factory=list)

    @property
    def n_windows(self) -> int:
        return len(self.windows)

    @property
    def total_arb_seconds(self) -> int:
        return sum(w.duration_seconds for w in self.windows)

    @property
    def durations(self) -> list[int]:
        return [w.duration_seconds for w in self.windows]

    @property
    def median_duration(self) -> float:
        return statistics.median(self.durations) if self.durations else 0.0

    @property
    def mean_duration(self) -> float:
        return statistics.mean(self.durations) if self.durations else 0.0

    @property
    def longest_window(self) -> int:
        return max(self.durations) if self.durations else 0

    @property
    def peak_profit(self) -> float:
        return max((w.peak_profit for w in self.windows), default=0.0)


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _seconds_between(ts1: str, ts2: str) -> int:
    return int((_parse_ts(ts2) - _parse_ts(ts1)).total_seconds())


def _find_windows(
    scans: list[tuple[str, ArbResult]],
    scan_interval: int = 30,
) -> list[ArbWindow]:
    """Group consecutive profitable scans into windows.

    A gap of more than 2× the scan interval breaks a window.
    """
    gap_threshold = scan_interval * 2

    windows: list[ArbWindow] = []
    run: list[tuple[str, ArbResult]] = []

    for ts, arb in scans:
        if not is_profitable(arb):
            if run:
                windows.append(_close_window(run))
                run = []
            continue

        if run:
            gap = _seconds_between(run[-1][0], ts)
            if gap > gap_threshold:
                windows.append(_close_window(run))
                run = []

        run.append((ts, arb))

    if run:
        windows.append(_close_window(run))

    return windows


def _close_window(run: list[tuple[str, ArbResult]]) -> ArbWindow:
    profits = [arb.net_profit for _, arb in run]
    # Use the most common direction in the window
    directions = [arb.direction for _, arb in run]
    direction = max(set(directions), key=directions.count)
    return ArbWindow(
        first_seen=run[0][0],
        last_seen=run[-1][0],
        duration_seconds=_seconds_between(run[0][0], run[-1][0]),
        n_scans=len(run),
        peak_profit=max(profits),
        mean_profit=statistics.mean(profits),
        direction=direction,
    )


def analyze_pair(
    db_path: str | Path,
    poly_cid: str,
    kalshi_ticker: str,
    fees: FeeParams = FeeParams(),
    scan_interval: int = 30,
    repo=None,
) -> PairLifetime:
    """Run lifetime analysis for a single matched pair.

    If *repo* is provided (a SnapshotRepository), uses it directly.
    Otherwise, creates one from *db_path*.
    """
    if repo is None:
        from polyarb.db.engine import create_engine
        from polyarb.db.repositories.snapshots import SqliteSnapshotRepository

        engine = create_engine(f"sqlite:///{db_path}")
        repo = SqliteSnapshotRepository(engine)

    rows = repo.get_pair_scans(poly_cid, kalshi_ticker)

    if not rows:
        return PairLifetime(
            poly_cid=poly_cid,
            kalshi_ticker=kalshi_ticker,
            poly_question="",
            kalshi_question="",
            total_scans=0,
            profitable_scans=0,
        )

    poly_q = rows[0]["poly_question"]
    kalshi_q = rows[0]["kalshi_question"]

    scans: list[tuple[str, ArbResult]] = []
    profitable_count = 0

    for r in rows:
        arb = compute_arb(
            r["poly_yes_ask"], r["poly_no_ask"],
            r["kalshi_yes_ask"], r["kalshi_no_ask"], fees,
        )
        scans.append((r["scan_ts"], arb))
        if is_profitable(arb):
            profitable_count += 1

    windows = _find_windows(scans, scan_interval)

    return PairLifetime(
        poly_cid=poly_cid,
        kalshi_ticker=kalshi_ticker,
        poly_question=poly_q,
        kalshi_question=kalshi_q,
        total_scans=len(rows),
        profitable_scans=profitable_count,
        windows=windows,
    )


def analyze_pairs(
    db_path: str | Path,
    pairs: list[tuple[str, str]],
    fees: FeeParams = FeeParams(),
    scan_interval: int = 30,
    repo=None,
) -> list[PairLifetime]:
    """Run lifetime analysis for multiple matched pairs."""
    return [
        analyze_pair(db_path, poly_cid, kalshi_ticker, fees, scan_interval, repo=repo)
        for poly_cid, kalshi_ticker in pairs
    ]


def summary(lifetimes: list[PairLifetime]) -> dict:
    """Aggregate statistics across all analyzed pairs."""
    all_windows: list[ArbWindow] = []
    all_durations: list[int] = []
    total_profitable = 0
    total_scans = 0

    for lt in lifetimes:
        all_windows.extend(lt.windows)
        all_durations.extend(lt.durations)
        total_profitable += lt.profitable_scans
        total_scans += lt.total_scans

    if not all_durations:
        return {
            "pairs_analyzed": len(lifetimes),
            "pairs_with_arbs": 0,
            "total_windows": 0,
            "total_profitable_scans": 0,
            "total_scans": total_scans,
            "median_duration_seconds": 0,
            "mean_duration_seconds": 0,
            "longest_window_seconds": 0,
            "shortest_window_seconds": 0,
            "peak_profit_per_contract": 0,
        }

    return {
        "pairs_analyzed": len(lifetimes),
        "pairs_with_arbs": sum(1 for lt in lifetimes if lt.windows),
        "total_windows": len(all_windows),
        "total_profitable_scans": total_profitable,
        "total_scans": total_scans,
        "median_duration_seconds": statistics.median(all_durations),
        "mean_duration_seconds": round(statistics.mean(all_durations), 1),
        "longest_window_seconds": max(all_durations),
        "shortest_window_seconds": min(all_durations),
        "peak_profit_per_contract": max(w.peak_profit for w in all_windows),
    }


def format_report(lifetimes: list[PairLifetime]) -> str:
    """Human-readable lifetime analysis report."""
    lines: list[str] = []
    stats = summary(lifetimes)

    lines.append("=" * 65)
    lines.append("LIFETIME ANALYSIS REPORT")
    lines.append("=" * 65)
    lines.append(f"Pairs analyzed:    {stats['pairs_analyzed']}")
    lines.append(f"Pairs with arbs:   {stats['pairs_with_arbs']}")
    lines.append(f"Total arb windows: {stats['total_windows']}")
    lines.append(f"Profitable scans:  {stats['total_profitable_scans']} / {stats['total_scans']}"
                 f" ({stats['total_profitable_scans']/max(stats['total_scans'],1)*100:.1f}%)")
    lines.append("")

    if stats["total_windows"] > 0:
        lines.append("Window durations:")
        lines.append(f"  Median:   {_fmt_duration(stats['median_duration_seconds'])}")
        lines.append(f"  Mean:     {_fmt_duration(stats['mean_duration_seconds'])}")
        lines.append(f"  Longest:  {_fmt_duration(stats['longest_window_seconds'])}")
        lines.append(f"  Shortest: {_fmt_duration(stats['shortest_window_seconds'])}")
        lines.append(f"  Peak profit/contract: ${stats['peak_profit_per_contract']:.4f}")
    lines.append("")

    lines.append("-" * 65)
    lines.append(f"{'Pair':40s} | {'Windows':>7s} | {'Median':>8s} | {'Peak $':>7s}")
    lines.append("-" * 65)

    for lt in sorted(lifetimes, key=lambda x: x.profitable_scans, reverse=True):
        label = f"{lt.poly_question[:38]}"
        med = _fmt_duration(lt.median_duration) if lt.windows else "-"
        peak = f"${lt.peak_profit:.4f}" if lt.windows else "-"
        lines.append(f"{label:40s} | {lt.n_windows:7d} | {med:>8s} | {peak:>7s}")

    lines.append("=" * 65)
    return "\n".join(lines)


def _fmt_duration(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60}s"
    h = s // 3600
    m = (s % 3600) // 60
    return f"{h}h {m}m"
