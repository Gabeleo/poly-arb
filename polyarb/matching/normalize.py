"""Text normalization for cross-platform market matching."""

from __future__ import annotations

import re

# Tokens that appear in almost every market question and carry no
# discriminating signal for matching purposes.
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "will",
        "be",
        "is",
        "are",
        "was",
        "were",
        "by",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "or",
        "and",
        "before",
        "after",
        "than",
        "from",
        "with",
        "into",
        "who",
        "what",
        "when",
        "where",
        "how",
        "which",
        "this",
        "that",
        "it",
        "its",
        "do",
        "does",
        "did",
        "has",
        "have",
        "had",
        "not",
        "no",
        "yes",
    }
)

_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def normalize(text: str) -> str:
    """Lowercase, expand abbreviations, strip non-alphanumeric, collapse whitespace."""
    text = text.lower()
    # Common abbreviations that split into misleading single-char tokens
    text = text.replace("u.s.", "us").replace("u.s", "us")
    text = re.sub(r"[^a-z0-9\s']", " ", text)
    return " ".join(text.split())


def tokenize(text: str) -> set[str]:
    """Extract meaningful tokens from market question text.

    Removes stop words; keeps numbers, names, and domain terms like
    'election', 'nomination', 'presidential' that distinguish markets.
    """
    tokens = set(_WORD_RE.findall(normalize(text)))
    return tokens - _STOP_WORDS


def extract_years(text: str) -> set[str]:
    """Extract four-digit years (19xx / 20xx) from text."""
    return set(_YEAR_RE.findall(text))
