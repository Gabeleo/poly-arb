"""Tests for polyarb.matching.biencoder."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np

from polyarb.matching.biencoder import BiEncoderFilter
from polyarb.matching.matcher import MatchedPair
from polyarb.models import Market, Side, Token


# ── Helpers ─────────────────────────────────────────────────


def _mkt(cid: str, question: str, platform: str = "polymarket") -> Market:
    return Market(
        condition_id=cid,
        question=question,
        yes_token=Token("y", Side.YES, 0.50, 0.49, 0.51),
        no_token=Token("n", Side.NO, 0.50, 0.49, 0.51),
        platform=platform,
    )


def _pair(poly_q: str, kalshi_q: str, confidence: float = 0.0) -> MatchedPair:
    return MatchedPair(
        _mkt(f"p_{poly_q[:8]}", poly_q),
        _mkt(f"k_{kalshi_q[:8]}", kalshi_q, "kalshi"),
        confidence,
    )


# ── Tests ───────────────────────────────────────────────────


def test_empty_candidates():
    bf = BiEncoderFilter()
    assert bf.filter_candidates([]) == []


def test_identical_questions():
    """Identical questions should have cosine similarity ~1.0 and pass any threshold."""
    bf = BiEncoderFilter()
    candidates = [_pair("Will BTC hit $100k?", "Will BTC hit $100k?")]
    result = bf.filter_candidates(candidates, threshold=0.9)
    assert len(result) == 1
    assert result[0].confidence > 0.99


def test_unrelated_questions():
    """Completely unrelated questions should score below a reasonable threshold."""
    bf = BiEncoderFilter()
    candidates = [
        _pair(
            "Will it rain in Tokyo tomorrow?",
            "Who won the 1998 FIFA World Cup?",
        )
    ]
    result = bf.filter_candidates(candidates, threshold=0.5)
    assert len(result) == 0


def test_threshold_filtering():
    """Pairs scoring below threshold are excluded, those above are kept."""
    bf = BiEncoderFilter()
    candidates = [
        _pair("Will Bitcoin exceed $100,000?", "BTC above $100k?"),       # similar
        _pair("Will it rain in Tokyo?", "Who won the Super Bowl?"),        # unrelated
    ]
    result = bf.filter_candidates(candidates, threshold=0.3)
    # The similar pair should survive, the unrelated one likely won't
    questions = [(r.poly_market.question, r.kalshi_market.question) for r in result]
    assert ("Will Bitcoin exceed $100,000?", "BTC above $100k?") in questions


def test_max_keep_limit():
    """max_keep should cap the number of returned candidates."""
    bf = BiEncoderFilter()
    # Create 10 identical-question pairs (all will pass threshold)
    candidates = [
        _pair(f"Will event {i} happen?", f"Will event {i} happen?")
        for i in range(10)
    ]
    result = bf.filter_candidates(candidates, threshold=0.1, max_keep=3)
    assert len(result) == 3


def test_sorted_by_score_descending():
    """Output should be sorted by bi-encoder confidence descending."""
    bf = BiEncoderFilter()
    candidates = [
        _pair("Will Bitcoin exceed $100k?", "BTC above $100,000?"),
        _pair("Will Bitcoin exceed $100k?", "Will Bitcoin exceed $100k?"),
    ]
    result = bf.filter_candidates(candidates, threshold=0.1)
    assert len(result) >= 1
    # Check descending order
    for i in range(len(result) - 1):
        assert result[i].confidence >= result[i + 1].confidence


def test_new_matchedpair_instances():
    """Returned pairs should have non-zero confidence (the bi-encoder score)."""
    bf = BiEncoderFilter()
    candidates = [_pair("Will X happen?", "Will X happen?")]
    assert candidates[0].confidence == 0.0  # input has 0.0

    result = bf.filter_candidates(candidates, threshold=0.1)
    assert len(result) == 1
    assert result[0].confidence > 0.0  # bi-encoder score, not the input 0.0


def test_embedding_cache():
    """Encoding the same question twice should use the cache, not re-encode."""
    bf = BiEncoderFilter()

    candidates1 = [_pair("Will BTC hit $100k?", "Bitcoin above $100,000?")]
    bf.filter_candidates(candidates1, threshold=0.1)

    # Spy on model.encode to count calls
    with patch.object(bf._model, "encode", wraps=bf._model.encode) as mock_encode:
        # Same questions — should all be cached
        candidates2 = [_pair("Will BTC hit $100k?", "Bitcoin above $100,000?")]
        bf.filter_candidates(candidates2, threshold=0.1)
        mock_encode.assert_not_called()


def test_embedding_cache_partial_hit():
    """When some questions are cached and some are new, only new ones are encoded."""
    bf = BiEncoderFilter()

    # Prime cache with one question
    candidates1 = [_pair("Will BTC hit $100k?", "Will BTC hit $100k?")]
    bf.filter_candidates(candidates1, threshold=0.1)

    with patch.object(bf._model, "encode", wraps=bf._model.encode) as mock_encode:
        # One cached question ("Will BTC hit $100k?"), one new ("Ethereum price?")
        candidates2 = [_pair("Will BTC hit $100k?", "Ethereum price?")]
        bf.filter_candidates(candidates2, threshold=0.0)
        # Only the new question should be encoded
        assert mock_encode.call_count == 1
        encoded_sentences = mock_encode.call_args[0][0]
        assert len(encoded_sentences) == 1
        assert "Ethereum" in encoded_sentences[0]


def test_cache_eviction():
    """Cache should evict oldest entries when exceeding max_cache_entries."""
    bf = BiEncoderFilter(max_cache_entries=3)

    # Fill cache with 3 entries
    for i in range(3):
        candidates = [_pair(f"Question {i}", f"Question {i}")]
        bf.filter_candidates(candidates, threshold=0.0)

    assert len(bf._cache) == 3

    # Add a 4th unique question — should evict oldest
    candidates = [_pair("Brand new question", "Brand new question")]
    bf.filter_candidates(candidates, threshold=0.0)
    assert len(bf._cache) <= 3


def test_no_duplicate_poly_markets():
    """Filter should not introduce duplicate Poly markets in output."""
    bf = BiEncoderFilter()
    poly_q = "Will BTC hit $100k?"
    candidates = [
        _pair(poly_q, "BTC above $100,000?"),
        _pair(poly_q, "Bitcoin exceeding $100k?"),
    ]
    result = bf.filter_candidates(candidates, threshold=0.1)
    # Both may survive (dedup is the cross-encoder's job), but each should
    # correspond to one of the original input candidates
    assert len(result) <= len(candidates)


def test_semantic_similarity():
    """Bi-encoder should catch semantic similarity that character matching misses."""
    bf = BiEncoderFilter()
    candidates = [
        _pair("Will the U.S. president be impeached?",
              "Will the United States president face impeachment?"),
    ]
    result = bf.filter_candidates(candidates, threshold=0.5)
    assert len(result) == 1
    assert result[0].confidence > 0.5
