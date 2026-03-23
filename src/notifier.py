"""Send Telegram notifications for detected whale trades."""

import logging

from telegram import Bot
from telegram.constants import ParseMode

from .config import config
from .analyzer import AnalysisResult
from .scorer import ScoreBreakdown

logger = logging.getLogger(__name__)

bot: Bot | None = None


def _score_bar(score: int, width: int = 10) -> str:
    """Render a visual score bar like [████░░░░░░] 40/100."""
    filled = round(score / 100 * width)
    return f"[{'█' * filled}{'░' * (width - filled)}] {score}/100"


def init_bot():
    """Initialize the Telegram bot instance."""
    global bot
    if not config.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not set — notifications disabled")
        return
    bot = Bot(token=config.telegram_bot_token)
    logger.info("Telegram bot initialized")


async def verify_bot() -> bool:
    """Initialize the bot and verify the token works.

    Call at startup to fail fast if credentials are wrong.
    Returns True if the bot is ready to send messages.
    """
    if not config.telegram_bot_token or not config.telegram_chat_id:
        logger.warning("Telegram not configured — running without notifications")
        return False
    init_bot()
    try:
        me = await bot.get_me()
        logger.info(f"Telegram bot verified: @{me.username}")
        return True
    except Exception as e:
        logger.error(f"Telegram bot token invalid: {e}")
        return False


async def send_whale_alert(
    analysis: AnalysisResult,
    trade_size_usdc: float,
    market_info: dict,
    side: str,
    tx_hash: str,
    exchange: str,
    score: ScoreBreakdown | None = None,
    entry_price: float | None = None,
) -> bool:
    """Send a formatted whale alert to the configured Telegram chat.

    Returns True if the message was sent successfully.
    """
    if bot is None:
        init_bot()
    if bot is None:
        return False

    # Build the alert message
    market_title = market_info.get("title", "Unknown market")
    outcome = market_info.get("outcome", "?")
    slug = market_info.get("slug", "")
    condition_id = market_info.get("condition_id", "")

    # Determine newness indicator
    if analysis.account_age_days <= 1:
        age_emoji = "🔴"
        age_label = "BRAND NEW"
    elif analysis.account_age_days <= 3:
        age_emoji = "🟠"
        age_label = "Very New"
    elif analysis.account_age_days <= 7:
        age_emoji = "🟡"
        age_label = "New"
    else:
        age_emoji = "⚪"
        age_label = "Recent"

    # Trade size formatting
    if trade_size_usdc >= 100_000:
        size_emoji = "🐋🐋🐋"
    elif trade_size_usdc >= 50_000:
        size_emoji = "🐋🐋"
    elif trade_size_usdc >= 10_000:
        size_emoji = "🐋"
    else:
        size_emoji = "🐟"

    # Direction emoji
    direction_emoji = "🟢 BUY" if side.upper() == "BUY" else "🔴 SELL"

    # Pseudonym display
    name_display = f' "{analysis.pseudonym}"' if analysis.pseudonym else ""

    # Build market link
    market_link = ""
    if slug:
        market_link = f"https://polymarket.com/event/{slug}"

    # Build score section
    score_section = ""
    if score is not None:
        # Build a compact breakdown showing which factors fired
        factors = []
        factor_labels = {
            "age": "Account Age",
            "low_prob": "Low Probability",
            "size": "Trade Size",
            "concentration": "Concentrated",
            "size_ratio": "Size vs History",
            "cluster": "Cluster Activity",
        }
        for key, pts in score.components.items():
            if pts > 0:
                label = factor_labels.get(key, key)
                factors.append(f"  • {label}: +{pts}")

        score_bar = _score_bar(score.total)
        score_section = (
            f"\n"
            f"{score.tier_emoji} <b>Suspicion Score: {score.total}/100 ({score.tier})</b>\n"
            f"{score_bar}\n"
            + "\n".join(factors)
            + "\n"
        )

    message = (
        f"{size_emoji} <b>New Whale Alert</b> {size_emoji}\n"
        f"\n"
        f"<b>Market:</b> {market_title}\n"
        f"<b>Direction:</b> {direction_emoji} {outcome}\n"
        f"<b>Size:</b> ${trade_size_usdc:,.2f} USDC\n"
        f"<b>Price:</b> {f'${entry_price:.2f} ({entry_price * 100:.0f}% implied)' if entry_price is not None else 'N/A'}\n"
        f"<b>Exchange:</b> {exchange}\n"
        f"\n"
        f"{age_emoji} <b>Account: {age_label}</b>\n"
        f"<b>Wallet:</b>{name_display}\n"
        f"<code>{analysis.wallet}</code>\n"
        f"<b>Prior trades:</b> {analysis.total_trades}\n"
        f"<b>Account age:</b> {analysis.account_age_days} days\n"
        f"<b>Total volume:</b> ${analysis.total_volume_usdc:,.2f}\n"
        f"<b>Markets traded:</b> {analysis.unique_markets}\n"
        f"{score_section}\n"
        f"🔗 <a href=\"{analysis.profile_url}\">Polymarket Profile</a>\n"
        f"🔗 <a href=\"{analysis.polygonscan_url}\">PolygonScan</a>\n"
    )

    if market_link:
        message += f"🔗 <a href=\"{market_link}\">View Market</a>\n"

    if tx_hash:
        message += f"🔗 <a href=\"https://polygonscan.com/tx/{tx_hash}\">Transaction</a>\n"

    try:
        await bot.send_message(
            chat_id=config.telegram_chat_id,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        logger.info(
            f"Alert sent: {analysis.wallet[:10]}... "
            f"${trade_size_usdc:,.0f} on {market_title[:40]}"
        )
        return True
    except Exception as e:
        logger.error(f"Failed to send Telegram alert: {e}")
        return False


async def send_status_message(text: str):
    """Send a plain status message (for startup, errors, etc.)."""
    if bot is None:
        init_bot()
    if bot is None:
        return
    try:
        await bot.send_message(
            chat_id=config.telegram_chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.error(f"Failed to send status message: {e}")
