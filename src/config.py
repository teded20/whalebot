"""Configuration loaded from environment variables."""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # Polygon RPC
    polygon_ws_rpc: str = os.getenv("POLYGON_WS_RPC", "")
    polygon_http_rpc: str = os.getenv("POLYGON_HTTP_RPC", "")

    # Telegram
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Database
    database_url: str = os.getenv("DATABASE_URL", "")

    # Detection thresholds
    min_trade_usdc: float = float(os.getenv("MIN_TRADE_USDC", "500"))
    max_prior_trades: int = int(os.getenv("MAX_PRIOR_TRADES", "10"))
    max_account_age_days: int = int(os.getenv("MAX_ACCOUNT_AGE_DAYS", "7"))
    polling_interval: int = int(os.getenv("POLLING_INTERVAL", "5"))

    def validate(self) -> list[str]:
        """Return list of missing required config fields."""
        errors = []
        if not self.polygon_ws_rpc and not self.polygon_http_rpc:
            errors.append("Need at least one of POLYGON_WS_RPC or POLYGON_HTTP_RPC")
        if not self.telegram_bot_token:
            errors.append("TELEGRAM_BOT_TOKEN is required")
        if not self.telegram_chat_id:
            errors.append("TELEGRAM_CHAT_ID is required")
        return errors


config = Config()
