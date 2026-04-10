"""Property-based tests for matching normalization and scoring."""

from __future__ import annotations

import string

from hypothesis import given, settings
from hypothesis.strategies import (
    sets,
    text,
)

from polyarb.matching.matcher import _containment, _jaccard
from polyarb.matching.normalize import _STOP_WORDS, extract_years, normalize, tokenize

# ── Strategies ───────────────────────────────────────────────

ascii_word = text(
    alphabet=string.ascii_lowercase + string.digits,
    min_size=1,
    max_size=15,
)
word_set = sets(ascii_word, min_size=1, max_size=20)
market_text = text(
    alphabet=string.ascii_letters + string.digits + " '?!.",
    min_size=1,
    max_size=200,
)


# ── normalize properties ─────────────────────────────────────


@given(t=market_text)
def test_normalize_idempotent(t):
    """Applying normalize twice gives the same result as once."""
    assert normalize(normalize(t)) == normalize(t)


@given(t=market_text)
def test_normalize_lowercase(t):
    result = normalize(t)
    assert result == result.lower()


@given(t=market_text)
def test_normalize_no_double_spaces(t):
    result = normalize(t)
    assert "  " not in result


# ── tokenize properties ──────────────────────────────────────


@given(t=market_text)
def test_tokenize_excludes_stop_words(t):
    tokens = tokenize(t)
    assert not (tokens & _STOP_WORDS)


@given(t=market_text)
def test_tokenize_all_lowercase(t):
    tokens = tokenize(t)
    for tok in tokens:
        assert tok == tok.lower()


@given(t=market_text)
def test_tokenize_subset_of_normalized(t):
    """Every token appears in the normalized text."""
    tokens = tokenize(t)
    normed = normalize(t)
    for tok in tokens:
        assert tok in normed


# ── extract_years properties ─────────────────────────────────


@given(t=market_text)
def test_extract_years_only_valid_years(t):
    years = extract_years(t)
    for y in years:
        assert len(y) == 4
        assert y.startswith("19") or y.startswith("20")


def test_extract_years_known_input():
    assert extract_years("Election 2024 vs 2028") == {"2024", "2028"}


def test_extract_years_no_match():
    assert extract_years("No year here") == set()


# ── Jaccard properties ───────────────────────────────────────


@given(a=word_set, b=word_set)
def test_jaccard_symmetric(a, b):
    assert abs(_jaccard(a, b) - _jaccard(b, a)) < 1e-10


@given(a=word_set)
def test_jaccard_self_is_one(a):
    assert _jaccard(a, a) == 1.0


@given(a=word_set, b=word_set)
def test_jaccard_bounded(a, b):
    score = _jaccard(a, b)
    assert 0.0 <= score <= 1.0


def test_jaccard_disjoint():
    assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0


def test_jaccard_empty():
    assert _jaccard(set(), {"a"}) == 0.0
    assert _jaccard(set(), set()) == 0.0


# ── Containment properties ───────────────────────────────────


@given(a=word_set, b=word_set)
def test_containment_symmetric(a, b):
    """Containment is symmetric: uses the smaller set."""
    assert abs(_containment(a, b) - _containment(b, a)) < 1e-10


@given(a=word_set)
def test_containment_self_is_one(a):
    assert _containment(a, a) == 1.0


@given(a=word_set, b=word_set)
def test_containment_bounded(a, b):
    score = _containment(a, b)
    assert 0.0 <= score <= 1.0


def test_containment_subset():
    """If a is a subset of b, containment is 1.0."""
    assert _containment({"a"}, {"a", "b", "c"}) == 1.0


def test_containment_empty():
    assert _containment(set(), {"a"}) == 0.0


# ── Score composition ────────────────────────────────────────


@given(a=word_set, b=word_set)
@settings(max_examples=100)
def test_year_mismatch_zeroes_score(a, b):
    """If both texts have years but no overlap, _score_pair returns 0."""
    from polyarb.matching.matcher import _score_pair

    score = _score_pair(a, b, "text 2024", "text 2025", {"2024"}, {"2025"})
    assert score == 0.0


def test_identical_text_high_score():
    """Identical normalized text should score very high."""
    from polyarb.matching.matcher import _score_pair

    tokens = tokenize("Will Bitcoin reach 100k?")
    norm = normalize("Will Bitcoin reach 100k?")
    score = _score_pair(tokens, tokens, norm, norm, set(), set())
    assert score >= 0.9
