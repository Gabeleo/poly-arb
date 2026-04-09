"""Generalized circuit breaker with consecutive-failure counting and exponential backoff.

Extracted from daemon/engine.py to be reusable across providers and
execution paths.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Defaults (match original daemon/engine.py values)
DEFAULT_THRESHOLD = 5
DEFAULT_MAX_DELAY = 300.0
DEFAULT_BASE_DELAY = 10.0


@dataclass
class CircuitBreaker:
    """Consecutive-failure counter with exponential backoff.

    Parameters
    ----------
    name : str
        Label for logging (e.g. "poly", "kalshi").
    threshold : int
        Number of consecutive failures before the circuit opens.
    max_delay : float
        Cap on the exponential backoff (seconds).
    base_delay : float
        Starting delay once the circuit opens. Doubles on each
        subsequent failure: ``base * 2^(failures - threshold)``.
    on_state_change : callable, optional
        Called with ``(name, is_open)`` whenever the state transitions.
    """

    name: str
    threshold: int = DEFAULT_THRESHOLD
    max_delay: float = DEFAULT_MAX_DELAY
    base_delay: float = DEFAULT_BASE_DELAY
    on_state_change: object = None  # Callable[[str, bool], None] | None
    _failures: int = field(default=0, init=False, repr=False)

    def record_success(self) -> None:
        """Reset the failure counter."""
        was_open = self.is_open
        if self._failures > 0:
            logger.info(
                "CircuitBreaker[%s] recovered after %d failures",
                self.name,
                self._failures,
            )
        self._failures = 0
        if was_open and self.on_state_change is not None:
            self.on_state_change(self.name, False)  # type: ignore[operator]

    def record_failure(self, exc: BaseException | None = None) -> None:
        """Increment the failure counter."""
        was_open = self.is_open
        self._failures += 1
        logger.warning(
            "CircuitBreaker[%s] failure %d/%d%s",
            self.name,
            self._failures,
            self.threshold,
            f": {exc}" if exc else "",
        )
        if not was_open and self.is_open and self.on_state_change is not None:
            self.on_state_change(self.name, True)  # type: ignore[operator]

    @property
    def is_open(self) -> bool:
        """True when the failure count has reached the threshold."""
        return self._failures >= self.threshold

    @property
    def failures(self) -> int:
        return self._failures

    @property
    def backoff_delay(self) -> float:
        """Seconds to wait before the next attempt (0 when closed)."""
        if not self.is_open:
            return 0.0
        return min(
            self.base_delay * (2 ** (self._failures - self.threshold)),
            self.max_delay,
        )

    def reset(self) -> None:
        """Force-reset the breaker (e.g. after manual intervention)."""
        self._failures = 0
