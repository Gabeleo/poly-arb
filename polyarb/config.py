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
