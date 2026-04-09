"""Pydantic request models for API input validation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ConfigUpdate(BaseModel):
    """Partial config update — only provided fields are changed."""

    model_config = ConfigDict(extra="forbid")

    min_profit: float | None = Field(None, ge=0)
    max_prob: float | None = Field(None, gt=0, le=1)
    scan_interval: float | None = Field(None, gt=0)
    order_size: float | None = Field(None, gt=0)
    kelly_fraction: float | None = Field(None, ge=0, le=1)
    max_position: float | None = Field(None, gt=0)
    bankroll: float | None = Field(None, ge=0)
    dedup_window: int | None = Field(None, gt=0)
    approval_timeout: float | None = Field(None, ge=0)
    digest_interval: float | None = Field(None, gt=0)
    match_candidate_threshold: float | None = Field(None, gt=0)
    match_final_threshold: float | None = Field(None, gt=0)
