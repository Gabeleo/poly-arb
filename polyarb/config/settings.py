"""Deploy-time configuration loaded once at startup from environment variables.

Settings are immutable after startup. If changing a value mid-operation could
cause data loss, financial risk, or infrastructure failure, it belongs here
and requires a redeploy.  Runtime-tunable trading parameters belong in Config.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Infrastructure
    database_url: str = "sqlite:///polyarb.db"
    encoder_url: str = ""
    api_key: str = ""

    # Kalshi credentials
    kalshi_api_key: str = ""
    kalshi_key_file: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_webhook_url: str = ""

    # Risk limits (deploy-time only — changing mid-operation is dangerous)
    max_total_exposure: float = 500.0
    max_daily_loss: float = 50.0
    max_position_per_market: int = 50
    max_concurrent_orders: int = 5
    max_single_order_size: int = 100
    min_time_between_trades: float = 30.0

    # Logging
    log_format: str = "json"
    log_level: str = "INFO"

    model_config = {"env_prefix": "POLYARB_"}
