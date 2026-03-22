"""Test Telegram integration: verify bot token and send a fake whale alert.

Usage: python test_telegram.py

Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
"""

import asyncio
import sys
import time

from src.config import config
from src.notifier import verify_bot, send_whale_alert, send_status_message
from src.analyzer import AnalysisResult


async def main():
    # Check config
    if not config.telegram_bot_token:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in .env")
        print("Get one from @BotFather on Telegram")
        sys.exit(1)
    if not config.telegram_chat_id:
        print("ERROR: TELEGRAM_CHAT_ID not set in .env")
        print("Send a message to your bot, then visit:")
        print(f"  https://api.telegram.org/bot{config.telegram_bot_token}/getUpdates")
        print("Look for 'chat': {'id': <number>} in the response")
        sys.exit(1)

    # Step 1: Verify bot token
    print("1. Verifying bot token...")
    ok = await verify_bot()
    if not ok:
        print("   FAILED — check your TELEGRAM_BOT_TOKEN")
        sys.exit(1)
    print("   OK")

    # Step 2: Send a status message
    print("2. Sending status message...")
    await send_status_message(
        "🧪 <b>Test message from Whale Bot</b>\n"
        "If you see this, Telegram integration is working!"
    )
    print("   Sent — check your Telegram chat")

    # Step 3: Send a fake whale alert
    print("3. Sending fake whale alert...")
    fake_analysis = AnalysisResult(
        wallet="0x1234567890abcdef1234567890abcdef12345678",
        is_new=True,
        total_trades=3,
        first_trade_timestamp=int(time.time()) - 3600,  # 1 hour ago
        account_age_days=0.0,
        total_volume_usdc=15420.50,
        pseudonym="Mysterious-Whale",
        timestamp=time.time(),
    )

    fake_market = {
        "title": "Will Bitcoin reach $150,000 by June 2026?",
        "slug": "will-bitcoin-reach-150k-june-2026",
        "outcome": "Yes",
        "event_slug": "bitcoin-price-milestones",
        "icon": "",
        "token_id": "12345",
        "condition_id": "0xabc123",
    }

    success = await send_whale_alert(
        analysis=fake_analysis,
        trade_size_usdc=25000.00,
        market_info=fake_market,
        side="BUY",
        tx_hash="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        exchange="CTF Exchange",
    )

    if success:
        print("   Sent — check your Telegram chat for the formatted alert")
    else:
        print("   FAILED — check logs above")
        sys.exit(1)

    print()
    print("All tests passed! Telegram integration is ready.")


if __name__ == "__main__":
    asyncio.run(main())
