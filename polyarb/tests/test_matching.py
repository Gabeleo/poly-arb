"""Tests for cross-platform market matching."""

from polyarb.matching.normalize import extract_years, normalize, tokenize
from polyarb.matching.matcher import MatchedPair, find_matches, _jaccard, _containment
from polyarb.models import Market, Side, Token


# ── Helpers ─────────────────────────────────────────────────


def _mkt(question: str, yes_mid: float = 0.50, platform: str = "polymarket") -> Market:
    no_mid = round(1 - yes_mid, 4)
    return Market(
        condition_id=f"test-{hash(question) % 10000}",
        question=question,
        yes_token=Token("y", Side.YES, yes_mid, yes_mid - 0.01, yes_mid + 0.01),
        no_token=Token("n", Side.NO, no_mid, no_mid - 0.01, no_mid + 0.01),
        platform=platform,
    )


# ── normalize.py ────────────────────────────────────────────


def test_normalize_basic():
    assert normalize("Will BTC Hit $100k?") == "will btc hit 100k"


def test_normalize_us_abbreviation():
    assert "us" in normalize("U.S. Presidential Election")
    # Should not produce separate 'u' and 's' tokens
    assert " u " not in f" {normalize('U.S. election')} "


def test_tokenize_removes_stop_words():
    tokens = tokenize("Will the president be in office?")
    assert "will" not in tokens
    assert "the" not in tokens
    assert "be" not in tokens
    assert "in" not in tokens
    assert "president" in tokens
    assert "office" in tokens


def test_tokenize_keeps_names_and_numbers():
    tokens = tokenize("Will Tim Walz win the 2028 election?")
    assert "tim" in tokens
    assert "walz" in tokens
    assert "2028" in tokens
    assert "election" in tokens


def test_extract_years():
    assert extract_years("before 2027") == {"2027"}
    assert extract_years("2028 presidential") == {"2028"}
    assert extract_years("no year here") == set()
    assert extract_years("1999 and 2025") == {"1999", "2025"}


# ── matcher.py primitives ───────────────────────────────────


def test_jaccard():
    assert _jaccard({"a", "b", "c"}, {"b", "c", "d"}) == 2 / 4
    assert _jaccard({"a"}, {"a"}) == 1.0
    assert _jaccard(set(), {"a"}) == 0.0


def test_containment():
    # Smaller set {a, b} fully contained in larger
    assert _containment({"a", "b"}, {"a", "b", "c"}) == 1.0
    # Smaller set {a, b}, only 'a' in larger
    assert _containment({"a", "b"}, {"a", "c", "d"}) == 0.5


# ── Matching: realistic market names ────────────────────────


def test_exact_same_question():
    """Same question on both platforms → high confidence."""
    poly = [_mkt("Will Bitcoin reach $150,000 in March?", 0.10)]
    kalshi = [_mkt("Will Bitcoin reach $150,000 in March?", 0.12, "kalshi")]
    matches = find_matches(poly, kalshi, min_confidence=0.3)
    assert len(matches) == 1
    assert matches[0].confidence > 0.9


def test_election_winner_match():
    """Same candidate, same election, different phrasing → should match."""
    poly = [_mkt("Will Tim Walz win the 2028 US Presidential Election?", 0.006)]
    kalshi = [
        _mkt("2028 U.S. Presidential Election winner? — Tim Walz", 0.002, "kalshi"),
    ]
    matches = find_matches(poly, kalshi, min_confidence=0.4)
    assert len(matches) == 1
    assert matches[0].confidence >= 0.5


def test_nomination_vs_election_no_match():
    """'Nomination' and 'election winner' are different questions."""
    poly = [_mkt("Will Gavin Newsom win the 2028 Democratic presidential nomination?")]
    kalshi = [
        _mkt("2028 U.S. Presidential Election winner? — Gavin Newsom", 0.18, "kalshi"),
    ]
    # Should NOT match at high confidence — nomination ≠ election
    matches = find_matches(poly, kalshi, min_confidence=0.6)
    assert len(matches) == 0


def test_year_mismatch_prevents_match():
    """Same topic but different years → no match."""
    poly = [_mkt("Will BTC hit $100k by 2025?")]
    kalshi = [_mkt("Will BTC hit $100k by 2026?", 0.50, "kalshi")]
    matches = find_matches(poly, kalshi, min_confidence=0.3)
    assert len(matches) == 0


def test_completely_different_markets():
    """Unrelated markets → no match."""
    poly = [_mkt("Will Israel launch a ground offensive in Lebanon?")]
    kalshi = [_mkt("What will the price of GTA VI be on PS5?", 0.50, "kalshi")]
    matches = find_matches(poly, kalshi, min_confidence=0.3)
    assert len(matches) == 0


def test_spread_calculation():
    """Spread = Kalshi YES − Poly YES."""
    poly = [_mkt("Will Tim Walz win the 2028 US Presidential Election?", 0.10)]
    kalshi = [
        _mkt("2028 U.S. Presidential Election winner? — Tim Walz", 0.15, "kalshi"),
    ]
    matches = find_matches(poly, kalshi, min_confidence=0.3)
    assert len(matches) >= 1
    assert matches[0].yes_spread == 0.05


def test_arb_profit_calculation():
    """Cross-platform arb profit uses ask prices, not midpoints."""
    # Poly YES ask=0.51, NO ask=0.51 (mid=0.50, spread ±0.01)
    # Kalshi YES ask=0.41, NO ask=0.61 (mid=0.40, spread ±0.01)
    # BUY YES Kalshi (0.41) + BUY NO Poly (0.51) = 0.92 → profit 0.08
    poly = [_mkt("Will X happen?", 0.50)]
    kalshi = [_mkt("Will X happen?", 0.40, "kalshi")]
    matches = find_matches(poly, kalshi, min_confidence=0.3)
    assert len(matches) == 1
    pair = matches[0]
    assert pair.profit_buy_kalshi_yes == round(1.0 - 0.41 - 0.51, 4)  # 0.08
    assert pair.profit_buy_poly_yes == round(1.0 - 0.51 - 0.61, 4)   # -0.12
    profit, side, _, _, price = pair.best_arb
    assert profit == round(1.0 - 0.41 - 0.51, 4)
    assert side == "yes"
    assert abs(price - 0.41) < 1e-9


def test_multiple_matches_sorted_by_confidence():
    """Multiple matches returned in confidence order."""
    poly = [
        _mkt("Will Tim Walz win the 2028 US Presidential Election?", 0.006),
        _mkt("GTA VI released before June 2026?", 0.40),
    ]
    kalshi = [
        _mkt("2028 U.S. Presidential Election winner? — Tim Walz", 0.002, "kalshi"),
        _mkt("GTA VI released before June 2026?", 0.45, "kalshi"),
    ]
    matches = find_matches(poly, kalshi, min_confidence=0.3)
    assert len(matches) >= 1
    # Sorted by confidence descending
    for i in range(len(matches) - 1):
        assert matches[i].confidence >= matches[i + 1].confidence


def test_best_match_picked_per_poly_market():
    """Each Polymarket market gets at most one (best) Kalshi match."""
    poly = [_mkt("Will Tim Walz win the 2028 US Presidential Election?")]
    kalshi = [
        _mkt("2028 U.S. Presidential Election winner? — Tim Walz", 0.50, "kalshi"),
        _mkt("2028 U.S. Presidential Election winner? — Gavin Newsom", 0.18, "kalshi"),
    ]
    matches = find_matches(poly, kalshi, min_confidence=0.3)
    # Should pick Tim Walz match, not Gavin Newsom
    assert len(matches) <= 1
    if matches:
        assert "Tim Walz" in matches[0].kalshi_market.question
