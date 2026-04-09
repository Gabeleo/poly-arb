"""Backtest engine for cross-platform prediction market arbitrage.

Replays recorded snapshots chronologically, enters one contract per arb
window when the cost model shows positive net profit, and holds to
settlement.  Tracks capital-in-use over time.

Entry rules:
  - Enter once per arb window (first profitable scan triggers entry)
  - 1 contract per trade
  - Profit locked at entry (hold to settlement)

Output: per-trade log, aggregate P&L, capital curve, return metrics.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from polyarb.analysis.costs import FeeParams, compute_arb, is_profitable


@dataclass(frozen=True)
class Trade:
    """A single backtest trade — one contract entered and held to settlement."""

    entry_ts: str
    poly_cid: str
    kalshi_ticker: str
    direction: str
    poly_ask: float
    kalshi_ask: float
    gross_cost: float
    poly_fee: float
    kalshi_fee: float
    total_cost: float  # gross_cost + fees (capital locked)
    net_profit: float  # 1.0 - total_cost
    settlement_date: str  # end_date / close_time
    days_to_settlement: float


@dataclass
class BacktestResult:
    """Full backtest output."""

    trades: list[Trade] = field(default_factory=list)
    # Capital curve: list of (scan_ts, capital_in_use)
    capital_curve: list[tuple[str, float]] = field(default_factory=list)
    fees: FeeParams = field(default_factory=FeeParams)

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def total_profit(self) -> float:
        return sum(t.net_profit for t in self.trades)

    @property
    def total_cost(self) -> float:
        return sum(t.total_cost for t in self.trades)

    @property
    def avg_profit(self) -> float:
        return self.total_profit / self.n_trades if self.trades else 0.0

    @property
    def max_capital_deployed(self) -> float:
        return max((c for _, c in self.capital_curve), default=0.0)

    @property
    def avg_days_to_settlement(self) -> float:
        if not self.trades:
            return 0.0
        return statistics.mean(t.days_to_settlement for t in self.trades)

    @property
    def median_days_to_settlement(self) -> float:
        if not self.trades:
            return 0.0
        return statistics.median(t.days_to_settlement for t in self.trades)

    @property
    def return_on_max_capital(self) -> float:
        mc = self.max_capital_deployed
        return self.total_profit / mc if mc > 0 else 0.0

    @property
    def profits(self) -> list[float]:
        return [t.net_profit for t in self.trades]

    @property
    def max_drawdown(self) -> float:
        """Max drawdown on cumulative P&L curve."""
        if not self.trades:
            return 0.0
        cumulative = 0.0
        peak = 0.0
        worst = 0.0
        for t in self.trades:
            cumulative += t.net_profit
            peak = max(peak, cumulative)
            worst = min(worst, cumulative - peak)
        return worst


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _days_between(ts1: str, ts2: str) -> float:
    dt1 = _parse_ts(ts1)
    dt2 = _parse_ts(ts2)
    return max(0.0, (dt2 - dt1).total_seconds() / 86400.0)


def run_backtest(
    db_path: str | Path,
    pairs: list[tuple[str, str]],
    fees: FeeParams | None = None,
    repo=None,
) -> BacktestResult:
    """Run the backtest over all matched pairs.

    Scans chronologically.  For each pair, enters on the first profitable
    scan of each arb window (consecutive profitable run).  Tracks capital
    locked until each trade's settlement date.

    If *repo* is provided (a SnapshotRepository), uses it directly.
    Otherwise, creates one from *db_path*.
    """
    if fees is None:
        fees = FeeParams()
    if repo is None:
        from polyarb.db.engine import create_engine
        from polyarb.db.repositories.snapshots import SqliteSnapshotRepository

        engine = create_engine(f"sqlite:///{db_path}")
        repo = SqliteSnapshotRepository(engine)

    result = BacktestResult(fees=fees)

    all_scans = repo.get_distinct_scan_timestamps()

    # Track per-pair state: was the previous scan profitable?
    # True = we're inside an arb window and already entered
    in_window: dict[tuple[str, str], bool] = {p: False for p in pairs}

    # Trades indexed by settlement date for capital release
    active_trades: list[Trade] = []

    for scan_ts in all_scans:
        # Compute capital-in-use at this scan
        capital = sum(t.total_cost for t in active_trades if t.settlement_date >= scan_ts)
        result.capital_curve.append((scan_ts, capital))

        # Evaluate each pair
        for poly_cid, kalshi_ticker in pairs:
            row = repo.get_pair_scan_at(poly_cid, kalshi_ticker, scan_ts)

            if row is None:
                in_window[(poly_cid, kalshi_ticker)] = False
                continue

            py_ask = row["poly_yes_ask"]
            pn_ask = row["poly_no_ask"]
            ky_ask = row["kalshi_yes_ask"]
            kn_ask = row["kalshi_no_ask"]
            p_end = row["end_date"]
            k_end = row["close_time"]
            arb = compute_arb(py_ask, pn_ask, ky_ask, kn_ask, fees)

            pair_key = (poly_cid, kalshi_ticker)

            if is_profitable(arb):
                if not in_window[pair_key]:
                    # First profitable scan of a new window → enter trade
                    settlement = p_end or k_end or scan_ts
                    days = _days_between(scan_ts, settlement)

                    trade = Trade(
                        entry_ts=scan_ts,
                        poly_cid=poly_cid,
                        kalshi_ticker=kalshi_ticker,
                        direction=arb.direction,
                        poly_ask=arb.poly_ask,
                        kalshi_ask=arb.kalshi_ask,
                        gross_cost=arb.gross_cost,
                        poly_fee=arb.poly_fee,
                        kalshi_fee=arb.kalshi_fee,
                        total_cost=arb.gross_cost + arb.poly_fee + arb.kalshi_fee,
                        net_profit=arb.net_profit,
                        settlement_date=settlement,
                        days_to_settlement=round(days, 2),
                    )
                    result.trades.append(trade)
                    active_trades.append(trade)
                    in_window[pair_key] = True
            else:
                # Window closed
                in_window[pair_key] = False

    return result


def format_report(result: BacktestResult) -> str:
    """Human-readable backtest report."""
    lines: list[str] = []

    lines.append("=" * 65)
    lines.append("BACKTEST REPORT")
    lines.append("=" * 65)
    lines.append(f"Trades executed:       {result.n_trades}")
    lines.append(f"Total P&L:             ${result.total_profit:.4f}")
    lines.append(f"Total capital deployed: ${result.total_cost:.4f}")
    lines.append(f"Avg profit/trade:      ${result.avg_profit:.4f}")
    lines.append(f"Max capital in use:    ${result.max_capital_deployed:.4f}")
    lines.append(f"Return on max capital: {result.return_on_max_capital:.2%}")
    lines.append(f"Max drawdown (P&L):    ${result.max_drawdown:.4f}")
    lines.append("")
    lines.append("Days to settlement:")
    lines.append(f"  Mean:   {result.avg_days_to_settlement:.1f} days")
    lines.append(f"  Median: {result.median_days_to_settlement:.1f} days")
    lines.append("")

    if result.trades:
        profits = result.profits
        lines.append("Profit distribution:")
        lines.append(f"  Min:    ${min(profits):.4f}")
        lines.append(f"  Median: ${statistics.median(profits):.4f}")
        lines.append(f"  Max:    ${max(profits):.4f}")
        lines.append("")

        # Per-pair summary
        pair_profits: dict[tuple[str, str], list[float]] = {}
        for t in result.trades:
            key = (t.poly_cid, t.kalshi_ticker)
            pair_profits.setdefault(key, []).append(t.net_profit)

        lines.append("-" * 65)
        lines.append(
            f"{'Pair (Poly CID)':20s} | {'Trades':>6s} | {'Total P&L':>10s} | {'Avg':>8s} | {'Days':>5s}"
        )
        lines.append("-" * 65)

        for (pcid, _ktick), profits_list in sorted(
            pair_profits.items(), key=lambda x: sum(x[1]), reverse=True
        ):
            total = sum(profits_list)
            avg = statistics.mean(profits_list)
            # Get avg days for this pair
            pair_trades = [t for t in result.trades if t.poly_cid == pcid]
            avg_days = statistics.mean(t.days_to_settlement for t in pair_trades)
            label = f"...{pcid[-8:]}"
            lines.append(
                f"{label:20s} | {len(profits_list):6d} | ${total:9.4f} | ${avg:7.4f} | {avg_days:5.1f}"
            )

    lines.append("=" * 65)
    return "\n".join(lines)
