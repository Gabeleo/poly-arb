"""Deterministic idempotency keys for order dedup.

Every execution gets a key derived from: match_key + direction + size +
timestamp_bucket.  The timestamp bucket uses 60-second windows so that
duplicate executions within the same window are rejected while allowing
re-execution in subsequent windows.
"""

from __future__ import annotations

import hashlib
import time


BUCKET_SECONDS = 60


def _timestamp_bucket(ts: float | None = None) -> int:
    """Return the current 60-second bucket as an integer."""
    if ts is None:
        ts = time.time()
    return int(ts) // BUCKET_SECONDS


def generate_idempotency_key(
    match_key: str,
    direction: str,
    size: float,
    ts: float | None = None,
) -> str:
    """Build a deterministic idempotency key.

    Parameters
    ----------
    match_key : str
        Composite key identifying the matched pair
        (e.g. "poly_cid:kalshi_cid").
    direction : str
        The trade direction (e.g. "kalshi_yes_poly_no").
    size : float
        Number of contracts.
    ts : float | None
        Unix timestamp.  Defaults to ``time.time()``.

    Returns
    -------
    str
        A 16-character hex digest that is stable within the same
        60-second window for the same inputs.
    """
    bucket = _timestamp_bucket(ts)
    payload = f"{match_key}|{direction}|{size}|{bucket}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
