"""Test data factories for generating realistic domain objects.

Usage in tests::

    from polyarb.tests.factories import MarketFactory, MatchedPairFactory

    market = MarketFactory.create(yes_mid=0.60, platform="kalshi")
    pair = MatchedPairFactory.create(spread=0.10)
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass

from polyarb.matching.matcher import MatchedPair
from polyarb.models import Market, Side, Token


def _random_id(prefix: str = "", length: int = 8) -> str:
    return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


_QUESTIONS = [
    "Will Bitcoin reach $100k by end of {year}?",
    "Will the Fed cut rates in {quarter} {year}?",
    "Will {name} win the election?",
    "Will GDP growth exceed {pct}% in {year}?",
    "Will unemployment drop below {pct}% by {month}?",
]

_NAMES = ["Biden", "Trump", "Harris", "DeSantis", "Newsom"]
_QUARTERS = ["Q1", "Q2", "Q3", "Q4"]
_MONTHS = ["January", "March", "June", "September", "December"]


def _random_question() -> str:
    template = random.choice(_QUESTIONS)
    return template.format(
        year=random.randint(2025, 2027),
        quarter=random.choice(_QUARTERS),
        name=random.choice(_NAMES),
        pct=round(random.uniform(1.0, 5.0), 1),
        month=random.choice(_MONTHS),
    )


@dataclass
class MarketFactory:
    """Factory for Market objects with realistic random defaults."""

    @staticmethod
    def create(
        cid: str | None = None,
        question: str | None = None,
        yes_mid: float | None = None,
        no_mid: float | None = None,
        platform: str = "polymarket",
        spread: float = 0.02,
    ) -> Market:
        cid = cid or _random_id("mkt-")
        question = question or _random_question()
        yes_mid = yes_mid if yes_mid is not None else round(random.uniform(0.10, 0.90), 4)
        no_mid = no_mid if no_mid is not None else round(1.0 - yes_mid, 4)
        half = spread / 2
        return Market(
            condition_id=cid,
            question=question,
            yes_token=Token(
                f"y-{cid}", Side.YES, yes_mid,
                round(yes_mid - half, 4), round(yes_mid + half, 4),
            ),
            no_token=Token(
                f"n-{cid}", Side.NO, no_mid,
                round(no_mid - half, 4), round(no_mid + half, 4),
            ),
            platform=platform,
        )

    @staticmethod
    def create_batch(n: int, **kwargs) -> list[Market]:
        return [MarketFactory.create(**kwargs) for _ in range(n)]


@dataclass
class MatchedPairFactory:
    """Factory for MatchedPair objects."""

    @staticmethod
    def create(
        poly_yes_mid: float = 0.55,
        kalshi_yes_mid: float = 0.45,
        confidence: float = 0.85,
        spread: float = 0.02,
    ) -> MatchedPair:
        poly = MarketFactory.create(
            cid=_random_id("poly-"),
            yes_mid=poly_yes_mid,
            platform="polymarket",
            spread=spread,
        )
        kalshi = MarketFactory.create(
            cid=_random_id("kalshi-"),
            question=poly.question,  # same question for matching
            yes_mid=kalshi_yes_mid,
            platform="kalshi",
            spread=spread,
        )
        return MatchedPair(poly_market=poly, kalshi_market=kalshi, confidence=confidence)

    @staticmethod
    def create_profitable(confidence: float = 0.9) -> MatchedPair:
        """Create a pair with a clear cross-platform arb (cost < $1)."""
        return MatchedPairFactory.create(
            poly_yes_mid=0.65,
            kalshi_yes_mid=0.40,
            confidence=confidence,
        )
