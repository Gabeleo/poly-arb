"""Match quality scoring — correlates confidence with actual profitability.

Answers: "Are high-confidence matches actually more profitable?"
Used to tune match_final_threshold empirically rather than by gut.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from polyarb.db.models import executions, match_snapshots


@dataclass(frozen=True)
class ConfidenceBucket:
    """Aggregated stats for matches in a confidence range."""

    bucket_min: float
    bucket_max: float
    match_count: int
    traded_count: int
    avg_confidence: float
    avg_profit: float | None  # None if no trades in this bucket

    @property
    def trade_rate(self) -> float:
        if self.match_count == 0:
            return 0.0
        return self.traded_count / self.match_count

    def to_dict(self) -> dict:
        return {
            "bucket": f"{self.bucket_min:.2f}-{self.bucket_max:.2f}",
            "match_count": self.match_count,
            "traded_count": self.traded_count,
            "trade_rate": round(self.trade_rate, 4),
            "avg_confidence": round(self.avg_confidence, 4),
            "avg_profit": round(self.avg_profit, 4) if self.avg_profit is not None else None,
        }


@dataclass(frozen=True)
class SignalReport:
    """Full signal quality analysis."""

    total_matches: int
    total_traded: int
    buckets: list[ConfidenceBucket]
    correlation: float | None  # Pearson-ish: positive = confidence predicts profit

    def to_dict(self) -> dict:
        return {
            "total_matches": self.total_matches,
            "total_traded": self.total_traded,
            "correlation": round(self.correlation, 4) if self.correlation is not None else None,
            "buckets": [b.to_dict() for b in self.buckets],
        }


# Default bucket edges: [0.5, 0.6), [0.6, 0.7), [0.7, 0.8), [0.8, 0.9), [0.9, 1.0]
DEFAULT_BUCKET_EDGES = [0.5, 0.6, 0.7, 0.8, 0.9, 1.01]


class SqliteSignalProvider:
    """Correlates match confidence with execution profitability."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def analyze(self, bucket_edges: list[float] | None = None) -> SignalReport:
        edges = bucket_edges or DEFAULT_BUCKET_EDGES
        pairs_with_confidence = self._get_pair_confidences()
        pair_profits = self._get_pair_profits()

        # Build buckets
        buckets: list[ConfidenceBucket] = []
        all_confidences: list[float] = []
        all_profits: list[float] = []

        for i in range(len(edges) - 1):
            lo, hi = edges[i], edges[i + 1]
            in_bucket = [(key, conf) for key, conf in pairs_with_confidence if lo <= conf < hi]
            match_count = len(in_bucket)
            traded = [(key, conf) for key, conf in in_bucket if key in pair_profits]
            traded_count = len(traded)

            avg_conf = sum(c for _, c in in_bucket) / match_count if match_count > 0 else 0.0
            avg_profit = None
            if traded_count > 0:
                avg_profit = sum(pair_profits[k] for k, _ in traded) / traded_count

            # Collect for correlation
            for key, conf in traded:
                all_confidences.append(conf)
                all_profits.append(pair_profits[key])

            buckets.append(
                ConfidenceBucket(
                    bucket_min=lo,
                    bucket_max=hi,
                    match_count=match_count,
                    traded_count=traded_count,
                    avg_confidence=avg_conf,
                    avg_profit=avg_profit,
                )
            )

        correlation = _pearson(all_confidences, all_profits)

        return SignalReport(
            total_matches=len(pairs_with_confidence),
            total_traded=len(pair_profits),
            buckets=buckets,
            correlation=correlation,
        )

    def _get_pair_confidences(self) -> list[tuple[str, float]]:
        """Return (match_key, avg_confidence) for all recorded pairs."""
        key_expr = match_snapshots.c.poly_condition_id + ":" + match_snapshots.c.kalshi_ticker
        stmt = select(
            key_expr.label("match_key"),
            func.avg(match_snapshots.c.confidence).label("avg_confidence"),
        ).group_by(key_expr)

        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [(r["match_key"], float(r["avg_confidence"])) for r in rows]

    def _get_pair_profits(self) -> dict[str, float]:
        """Return {match_key: total_profit} for executed pairs."""
        stmt = (
            select(
                executions.c.match_key,
                func.sum(executions.c.profit).label("total_profit"),
            )
            .where(executions.c.status == "completed")
            .where(executions.c.profit.isnot(None))
            .group_by(executions.c.match_key)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return {r["match_key"]: float(r["total_profit"]) for r in rows}


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Simple Pearson correlation. Returns None if < 2 data points."""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)
