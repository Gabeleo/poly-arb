from dataclasses import dataclass


@dataclass
class Config:
    min_profit: float = 0.005
    max_prob: float = 0.95
    scan_interval: float = 10.0
    order_size: float = 10.0
    dedup_window: int = 60
    approval_timeout: float = 120.0
    digest_interval: float = 3600.0
    match_candidate_threshold: float = 0.15
    match_final_threshold: float = 0.5

    def __post_init__(self) -> None:
        if self.min_profit < 0:
            raise ValueError("min_profit must be >= 0")
        if not (0.0 < self.max_prob <= 1.0):
            raise ValueError("max_prob must be in (0.0, 1.0]")
        if self.scan_interval <= 0:
            raise ValueError("scan_interval must be > 0")
        if self.order_size <= 0:
            raise ValueError("order_size must be > 0")
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
