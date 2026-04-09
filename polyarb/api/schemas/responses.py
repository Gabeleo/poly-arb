"""Pydantic response models for typed API responses and OpenAPI generation."""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    healthy: bool
    checks: dict[str, str]


class StatusResponse(BaseModel):
    uptime_seconds: float
    scan_count: int
    connected_clients: int
    match_count: int
    opportunity_count: int
    kelly_enabled: bool
    bankroll: float
    kelly_fraction: float
    biencoder_enabled: bool


class ConfigResponse(BaseModel):
    min_profit: float
    max_prob: float
    scan_interval: float
    order_size: float
    kelly_fraction: float
    max_position: float
    bankroll: float
    dedup_window: int
    approval_timeout: float
    digest_interval: float
    match_candidate_threshold: float
    match_final_threshold: float


class ErrorResponse(BaseModel):
    error: str
