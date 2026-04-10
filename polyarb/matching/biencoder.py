"""Bi-encoder pre-filter for cross-platform candidate pairs.

Uses sentence-transformers to compute embeddings locally, then scores
candidates via cosine similarity.  Runs between ``generate_all_pairs``
(cheap character-level ranking) and the cross-encoder verification
(expensive GPU sidecar call), removing obvious non-matches so the
cross-encoder sees fewer pairs.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict

import numpy as np
from sentence_transformers import SentenceTransformer

from polyarb.matching.matcher import MatchedPair

logger = logging.getLogger(__name__)


class BiEncoderFilter:
    """Local embedding-based candidate filter.

    Loads a sentence-transformer model once, then reuses it across calls.
    Embeddings are cached by question text so repeated markets across
    scans don't require re-encoding.
    """

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        max_cache_entries: int = 2000,
    ) -> None:
        self._model = SentenceTransformer(model_name)
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._max_cache = max_cache_entries
        self._lock = threading.Lock()

    def _get_embeddings(self, sentences: list[str]) -> dict[str, np.ndarray]:
        """Encode sentences, using cache where possible.

        Returns a mapping from sentence text to its embedding vector.
        """
        with self._lock:
            to_encode: list[str] = []
            for s in sentences:
                if s not in self._cache:
                    to_encode.append(s)

        if to_encode:
            vectors = self._model.encode(to_encode, batch_size=64)
            with self._lock:
                for s, vec in zip(to_encode, vectors, strict=True):
                    self._cache[s] = vec
                    self._cache.move_to_end(s)

                # Evict oldest entries if cache is over capacity
                while len(self._cache) > self._max_cache:
                    self._cache.popitem(last=False)

        with self._lock:
            return {s: self._cache[s] for s in sentences}

    def filter_candidates(
        self,
        candidates: list[MatchedPair],
        threshold: float = 0.15,
        max_keep: int = 50,
    ) -> list[MatchedPair]:
        """Score candidates via embedding cosine similarity.

        For each candidate pair:
        1. Encode poly question and kalshi question
        2. Compute cosine similarity
        3. Filter pairs below threshold
        4. Keep top max_keep by score
        5. Return new MatchedPair instances with bi-encoder score
           as the confidence value

        Returns candidates sorted by bi-encoder score (descending).
        """
        if not candidates:
            return []

        # Deduplicate questions before encoding
        unique_questions: set[str] = set()
        for c in candidates:
            unique_questions.add(c.poly_market.question)
            unique_questions.add(c.kalshi_market.question)

        embeddings = self._get_embeddings(list(unique_questions))

        # Score each pair via cosine similarity
        scored: list[tuple[float, MatchedPair]] = []
        for c in candidates:
            vec_a = embeddings[c.poly_market.question]
            vec_b = embeddings[c.kalshi_market.question]
            norm_a = np.linalg.norm(vec_a)
            norm_b = np.linalg.norm(vec_b)
            if norm_a == 0 or norm_b == 0:
                sim = 0.0
            else:
                sim = float(np.dot(vec_a, vec_b) / (norm_a * norm_b))
            if sim >= threshold:
                scored.append((sim, c))

        # Sort by score descending, keep top max_keep
        scored.sort(key=lambda t: t[0], reverse=True)
        top = scored[:max_keep]

        return [MatchedPair(c.poly_market, c.kalshi_market, score) for score, c in top]
