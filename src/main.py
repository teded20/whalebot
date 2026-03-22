"""Entry point for the Polymarket Whale Detector Bot."""

import asyncio
import logging
import sys

from .config import config
from .database import init_db, close_db
from .monitor import run_monitor
from .notifier import verify_bot
from .settlement import check_settlements

SETTLEMENT_INTERVAL = 6 * 60 * 60  # 6 hours


def setup_logging():
    """Configure structured logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )
    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("web3").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main():
    setup_logging()
    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("  Polymarket New Whale Detector Bot")
    logger.info("=" * 60)

    # Validate config — RPC is required, Telegram is optional
    if not config.polygon_ws_rpc and not config.polygon_http_rpc:
        logger.error("Config error: Need at least one of POLYGON_WS_RPC or POLYGON_HTTP_RPC")
        logger.error("Fix the above errors in your .env file and restart.")
        sys.exit(1)

    logger.info(f"Min trade size:       ${config.min_trade_usdc:,.0f} USDC")
    logger.info(f"Max prior trades:     {config.max_prior_trades}")
    logger.info(f"Max account age:      {config.max_account_age_days} days")
    logger.info(f"RPC (WS):             {'configured' if config.polygon_ws_rpc else 'not set'}")
    logger.info(f"RPC (HTTP):           {'configured' if config.polygon_http_rpc else 'not set'}")
    logger.info(f"Telegram:             {'configured' if config.telegram_bot_token else 'NOT SET (log-only mode)'}")
    logger.info(f"Database:             {'configured' if config.database_url else 'NOT SET (no persistence)'}")
    logger.info("")

    async def settlement_loop():
        """Run settlement checks periodically in the background."""
        while True:
            await asyncio.sleep(SETTLEMENT_INTERVAL)
            try:
                await check_settlements()
            except Exception as e:
                logger.error(f"Settlement check failed: {e}")

    async def start():
        # Initialize database
        if config.database_url:
            await init_db()
            logger.info("Database connected — signals will be persisted")
        else:
            logger.warning("No DATABASE_URL — running without persistence")

        # Verify Telegram at startup (non-blocking — runs without it)
        await verify_bot()

        # Start settlement checker in background
        if config.database_url:
            asyncio.create_task(settlement_loop())

        await run_monitor()

    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
