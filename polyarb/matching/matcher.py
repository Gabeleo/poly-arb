"""Cross-platform market matching.

Scores how likely two markets (from different platforms) refer to the
same real-world question, using token overlap, containment, and
sequence similarity.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Protocol

from polyarb.matching.normalize import extract_years, normalize, tokenize
from polyarb.models import Market


@dataclass(frozen=True)
class MatchedPair:
    """A pair of markets from different platforms believed to represent
    the same real-world question."""

    poly_market: Market
    kalshi_market: Market
    confidence: float  # 0.0–1.0

    @property
    def yes_spread(self) -> float:
        """YES midpoint difference (indicator only, not executable)."""
        return round(
            self.kalshi_market.yes_token.midpoint
            - self.poly_market.yes_token.midpoint,
            4,
        )

    # ── Arb profit at executable (ask) prices ───────────

    @property
    def profit_buy_kalshi_yes(self) -> float:
        """Profit/share: BUY YES on Kalshi + BUY NO on Polymarket.

        Both pay $1.00 in complementary outcomes, guaranteeing $1 payout.
        """
        cost = self.kalshi_market.yes_token.best_ask + self.poly_market.no_token.best_ask
        return round(1.0 - cost, 4)

    @property
    def profit_buy_poly_yes(self) -> float:
        """Profit/share: BUY YES on Polymarket + BUY NO on Kalshi."""
        cost = self.poly_market.yes_token.best_ask + self.kalshi_market.no_token.best_ask
        return round(1.0 - cost, 4)

    @property
    def best_arb(self) -> tuple[float, str, str, str, float]:
        """(profit, kalshi_side, kalshi_action_desc, poly_action_desc, kalshi_price).

        Returns the most profitable direction, or the least negative.
        """
        p1 = self.profit_buy_kalshi_yes
        p2 = self.profit_buy_poly_yes
        if p1 >= p2:
            return (
                p1,
                "yes",
                "BUY YES on Kalshi",
                "BUY NO on Polymarket",
                self.kalshi_market.yes_token.best_ask,
            )
        return (
            p2,
            "no",
            "BUY NO on Kalshi",
            "BUY YES on Polymarket",
            self.kalshi_market.no_token.best_ask,
        )

    @property
    def execution_params(self) -> dict:
        """Structured execution parameters for both platforms.

        Returns a dict with ``profit``, ``kalshi`` (ticker, side, price),
        and ``poly`` (token_id, side, price) derived from ``best_arb``.
        """
        profit, kalshi_side, _, _, kalshi_price = self.best_arb
        poly_side = "no" if kalshi_side == "yes" else "yes"
        poly_token = (
            self.poly_market.no_token if poly_side == "no"
            else self.poly_market.yes_token
        )
        return {
            "profit": profit,
            "kalshi": {
                "ticker": self.kalshi_market.condition_id,
                "side": kalshi_side,
                "price": kalshi_price,
            },
            "poly": {
                "token_id": poly_token.token_id,
                "side": poly_side,
                "price": poly_token.best_ask,
            },
        }

    def to_dict(self) -> dict:
        profit, kalshi_side, kalshi_desc, poly_desc, kalshi_price = self.best_arb
        return {
            "poly_market": self.poly_market.to_dict(),
            "kalshi_market": self.kalshi_market.to_dict(),
            "confidence": self.confidence,
            "yes_spread": self.yes_spread,
            "profit_buy_kalshi_yes": self.profit_buy_kalshi_yes,
            "profit_buy_poly_yes": self.profit_buy_poly_yes,
            "best_arb": {
                "profit": profit,
                "kalshi_side": kalshi_side,
                "kalshi_desc": kalshi_desc,
                "poly_desc": poly_desc,
                "kalshi_price": kalshi_price,
            },
        }


# ── Scoring internals ──────────────────────────────────────────


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _containment(a: set[str], b: set[str]) -> float:
    """Fraction of the *smaller* set found in the larger."""
    if not a or not b:
        return 0.0
    smaller, larger = (a, b) if len(a) <= len(b) else (b, a)
    return len(smaller & larger) / len(smaller)


def _score_pair(
    poly_tokens: set[str],
    kalshi_tokens: set[str],
    poly_norm: str,
    kalshi_norm: str,
    poly_years: set[str],
    kalshi_years: set[str],
) -> float:
    """Score 0.0–1.0 whether two pre-tokenized markets match.

    Three signals, weighted:
      40 %  Jaccard token overlap
      35 %  Containment (smaller set in larger)
      25 %  SequenceMatcher ratio on normalized text
    """
    # Hard filter: if both mention years but none overlap → no match
    if poly_years and kalshi_years and not (poly_years & kalshi_years):
        return 0.0

    jaccard = _jaccard(poly_tokens, kalshi_tokens)
    containment = _containment(poly_tokens, kalshi_tokens)
    seq = SequenceMatcher(None, poly_norm, kalshi_norm).ratio()

    return round(0.40 * jaccard + 0.35 * containment + 0.25 * seq, 3)


# ── Public API ──────────────────────────────────────────────────


def find_matches(
    poly_markets: list[Market],
    kalshi_markets: list[Market],
    min_confidence: float = 0.5,
) -> list[MatchedPair]:
    """Find cross-platform market pairs above *min_confidence*.

    For each Polymarket market, picks the single best Kalshi match.
    Returns results sorted by confidence (descending).
    """
    # Pre-compute tokens, norms, and years for every market
    poly_data = [
        (m, tokenize(m.question), normalize(m.question), extract_years(m.question))
        for m in poly_markets
    ]
    kalshi_data = [
        (m, tokenize(m.question), normalize(m.question), extract_years(m.question))
        for m in kalshi_markets
    ]

    # Inverted index: token → list of kalshi indices that contain it.
    # Lets us skip pairs that share zero tokens.
    kalshi_index: dict[str, list[int]] = {}
    for i, (_, tokens, _, _) in enumerate(kalshi_data):
        for t in tokens:
            kalshi_index.setdefault(t, []).append(i)

    matches: list[MatchedPair] = []

    for pm, p_tok, p_norm, p_years in poly_data:
        # Only consider Kalshi markets sharing at least one token
        candidate_ids: set[int] = set()
        for t in p_tok:
            for idx in kalshi_index.get(t, []):
                candidate_ids.add(idx)

        best_score = 0.0
        best_km: Market | None = None

        for idx in candidate_ids:
            km, k_tok, k_norm, k_years = kalshi_data[idx]
            score = _score_pair(p_tok, k_tok, p_norm, k_norm, p_years, k_years)
            if score > best_score:
                best_score = score
                best_km = km

        if best_km is not None and best_score >= min_confidence:
            matches.append(MatchedPair(pm, best_km, best_score))

    matches.sort(key=lambda m: m.confidence, reverse=True)
    return matches


def generate_all_pairs(
    poly_markets: list[Market],
    kalshi_markets: list[Market],
    max_candidates: int = 200,
) -> list[MatchedPair]:
    """Generate candidate pairs for cross-encoder verification.

    Computes the full cartesian product, applies the year-mismatch hard
    filter, then ranks by cheap character-level similarity (SequenceMatcher)
    and returns the top *max_candidates*.  SequenceMatcher works on raw
    character sequences so it catches overlap that token-based methods miss
    (e.g. 'BTC' vs 'Bitcoin' share the 'bt' prefix).
    """
    poly_norm = [normalize(m.question) for m in poly_markets]
    kalshi_norm = [normalize(m.question) for m in kalshi_markets]
    poly_years = [extract_years(m.question) for m in poly_markets]
    kalshi_years = [extract_years(m.question) for m in kalshi_markets]

    scored: list[tuple[float, int, int]] = []
    for i, pm in enumerate(poly_markets):
        py = poly_years[i]
        pn = poly_norm[i]
        for j, km in enumerate(kalshi_markets):
            ky = kalshi_years[j]
            if py and ky and not (py & ky):
                continue
            # Cheap character-level score for ranking (no token overlap needed)
            score = SequenceMatcher(None, pn, kalshi_norm[j]).ratio()
            scored.append((score, i, j))

    # Take top candidates by cheap score
    scored.sort(key=lambda t: t[0], reverse=True)
    top = scored[:max_candidates]

    return [
        MatchedPair(poly_markets[i], kalshi_markets[j], 0.0)
        for _, i, j in top
    ]


# ── MatchingStrategy protocol ─────────────────────────────────────


class MatchingStrategy(Protocol):
    """Protocol for cross-platform market matching strategies."""

    async def match(
        self,
        poly_markets: list[Market],
        kalshi_markets: list[Market],
        threshold: float,
    ) -> list[MatchedPair]:
        """Return matched pairs above the given confidence threshold."""
        ...


class TokenMatcher:
    """Token-based matching using Jaccard, containment, and SequenceMatcher."""

    async def match(
        self,
        poly_markets: list[Market],
        kalshi_markets: list[Market],
        threshold: float,
    ) -> list[MatchedPair]:
        import asyncio
        return await asyncio.to_thread(
            find_matches, poly_markets, kalshi_markets, threshold,
        )


class EncoderMatcher:
    """Two-phase matching: generate all pairs, then verify with cross-encoder."""

    def __init__(self, encoder_client, final_threshold: float = 0.5, biencoder=None) -> None:
        self._encoder = encoder_client
        self._threshold = final_threshold
        self._biencoder = biencoder

    async def match(
        self,
        poly_markets: list[Market],
        kalshi_markets: list[Market],
        threshold: float,
    ) -> list[MatchedPair]:
        import asyncio
        from polyarb.daemon.engine import _verify_candidates

        candidates = await asyncio.to_thread(
            generate_all_pairs, poly_markets, kalshi_markets,
        )
        if self._biencoder is not None:
            candidates = await asyncio.to_thread(
                self._biencoder.filter_candidates, candidates, threshold,
            )
        return await _verify_candidates(candidates, self._encoder, self._threshold)
