from dataclasses import dataclass


@dataclass
class Config:
    min_profit: float = 0.005
    max_prob: float = 0.95
    scan_interval: float = 10.0
    order_size: float = 10.0
    kelly_fraction: float = 0.0
    max_position: float = 100.0
    bankroll: float = 0.0
    dedup_window: int = 60
    approval_timeout: float = 120.0
    digest_interval: float = 3600.0
    match_candidate_threshold: float = 0.15
    match_final_threshold: float = 0.5
    fetch_timeout: float = 30.0
    fetch_retries: int = 2
    provider_timeout: float = 15.0
    encoder_timeout: float = 60.0

    def __post_init__(self) -> None:  # noqa: C901
        if self.min_profit < 0:
            raise ValueError("min_profit must be >= 0")
        if not (0.0 < self.max_prob <= 1.0):
            raise ValueError("max_prob must be in (0.0, 1.0]")
        if self.scan_interval <= 0:
            raise ValueError("scan_interval must be > 0")
        if self.order_size <= 0:
            raise ValueError("order_size must be > 0")
        if not (0.0 <= self.kelly_fraction <= 1.0):
            raise ValueError("kelly_fraction must be in [0.0, 1.0]")
        if self.max_position <= 0:
            raise ValueError("max_position must be > 0")
        if self.bankroll < 0:
            raise ValueError("bankroll must be >= 0.0")
        if self.dedup_window <= 0:
            raise ValueError("dedup_window must be > 0")
        if self.approval_timeout < 0:
            raise ValueError("approval_timeout must be >= 0")
        if self.digest_interval <= 0:
            raise ValueError("digest_interval must be > 0")
        if self.match_candidate_threshold <= 0:
            raise ValueError("match_candidate_threshold must be > 0")
        if self.match_final_threshold <= 0:
            raise ValueError("match_final_threshold must be > 0")
        if self.fetch_timeout <= 0:
            raise ValueError("fetch_timeout must be > 0")
        if self.fetch_retries < 0:
            raise ValueError("fetch_retries must be >= 0")
        if self.provider_timeout <= 0:
            raise ValueError("provider_timeout must be > 0")
        if self.encoder_timeout <= 0:
            raise ValueError("encoder_timeout must be > 0")
        # Cross-field validations
        if self.kelly_fraction > 0 and self.bankroll == 0:
            raise ValueError(
                "kelly_fraction > 0 requires bankroll > 0 (set bankroll or disable Kelly with kelly_fraction=0)"
            )
        if self.match_candidate_threshold >= self.match_final_threshold:
            raise ValueError(
                f"match_candidate_threshold ({self.match_candidate_threshold}) "
                f"must be < match_final_threshold ({self.match_final_threshold})"
            )
