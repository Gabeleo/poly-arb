"""Periodic report generation — daily and weekly summaries.

Combines P&L, performance, and signal data into structured reports
suitable for Telegram digests, API responses, or logging.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from polyarb.analytics.performance import SqlitePerformanceProvider
from polyarb.analytics.pnl import SqlitePnLProvider
from polyarb.analytics.signals import SqliteSignalProvider


@dataclass(frozen=True)
class PeriodReport:
    """Summary report for a time period."""

    period: str  # "daily" or "weekly"
    generated_at: str
    pnl: dict
    performance: dict
    signal_quality: dict

    def to_dict(self) -> dict:
        return {
            "period": self.period,
            "generated_at": self.generated_at,
            "pnl": self.pnl,
            "performance": self.performance,
            "signal_quality": self.signal_quality,
        }


class ReportGenerator:
    """Generates daily and weekly summary reports."""

    def __init__(self, engine) -> None:
        self._pnl = SqlitePnLProvider(engine)
        self._perf = SqlitePerformanceProvider(engine)
        self._signals = SqliteSignalProvider(engine)

    def daily(self) -> PeriodReport:
        return PeriodReport(
            period="daily",
            generated_at=datetime.now(UTC).isoformat(),
            pnl=self._pnl_section(lookback_days=1),
            performance=self._perf.summary().to_dict(),
            signal_quality=self._signals.analyze().to_dict(),
        )

    def weekly(self) -> PeriodReport:
        return PeriodReport(
            period="weekly",
            generated_at=datetime.now(UTC).isoformat(),
            pnl=self._pnl_section(lookback_days=7),
            performance=self._perf.summary().to_dict(),
            signal_quality=self._signals.analyze().to_dict(),
        )

    def _pnl_section(self, lookback_days: int) -> dict:
        summary = self._pnl.summary().to_dict()
        daily = [d.to_dict() for d in self._pnl.daily(lookback_days=lookback_days)]
        summary["daily_breakdown"] = daily
        return summary

    def format_text(self, report: PeriodReport) -> str:
        """Format a report as plain text for Telegram/logging."""
        pnl = report.pnl
        perf = report.performance
        lines = [
            f"=== {report.period.upper()} REPORT ({report.generated_at[:10]}) ===",
            "",
            f"P&L: ${pnl['total']:+.2f} (realized ${pnl['total_realized']:+.2f}, "
            f"unrealized ${pnl['total_unrealized']:+.2f})",
            f"Open positions: {pnl['open_positions']} | Closed: {pnl['closed_positions']}",
            "",
            f"Trades: {perf['total_trades']} | Win rate: {perf['win_rate']:.0%}",
            f"Total profit: ${perf['total_profit']:+.2f}",
        ]

        if perf["by_pair"]:
            lines.append("")
            lines.append("Top pairs:")
            for p in perf["by_pair"][:5]:
                lines.append(
                    f"  {p['match_key']}: ${p['total_profit']:+.2f} "
                    f"({p['trade_count']} trades, {p['win_rate']:.0%} win)"
                )

        sig = report.signal_quality
        if sig["correlation"] is not None:
            lines.append("")
            lines.append(
                f"Signal quality: confidence-profit correlation = {sig['correlation']:+.3f}"
            )

        return "\n".join(lines)
