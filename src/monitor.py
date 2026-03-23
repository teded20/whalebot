"""Monitor Polymarket exchange contracts for large trades by new accounts.

Supports two modes:
1. WebSocket subscription (preferred) — real-time event streaming
2. HTTP polling (fallback) — polls for new blocks periodically

The monitor watches both CTF Exchange and NegRisk CTF Exchange for
OrderFilled events, extracts the trade details, and hands off to the
analyzer and notifier.
"""

import asyncio
import json
import logging
import time
from typing import Optional

import httpx
from web3 import Web3
from web3.contract import Contract

from .config import config
from .contracts import (
    CTF_EXCHANGE,
    NEG_RISK_CTF_EXCHANGE,
    EXCHANGE_ABI,
    USDC_DECIMALS,
    KNOWN_CONTRACTS,
)
from .analyzer import analyze_wallet
from .database import save_signal, count_recent_new_wallets_on_outcome
from .market_resolver import resolve_market, get_current_price
from .notifier import send_whale_alert, send_status_message
from .scorer import score_trade

logger = logging.getLogger(__name__)

# Track the last processed block to avoid duplicates
_last_block: int = 0

# Rate-limit API calls: track wallets we've recently checked
_recent_checks: dict[str, float] = {}
CHECK_COOLDOWN = 60  # Don't re-check same wallet within 60 seconds


def _get_usdc_amount(raw_amount: int) -> float:
    """Convert raw USDC integer to float (6 decimals)."""
    return raw_amount / (10**USDC_DECIMALS)


def _is_collateral_side(asset_id: int) -> bool:
    """Check if this assetId represents the USDC/collateral side of a trade.

    In Polymarket's CTF Exchange, collateral (USDC) is represented by
    assetId = 0, while CTF outcome tokens have 77-78 digit uint256 IDs.
    """
    return asset_id == 0


async def process_order_filled(
    event: dict,
    exchange_name: str,
    http_client: httpx.AsyncClient,
):
    """Process a single OrderFilled event.

    1. Extract maker/taker addresses and trade amounts
    2. Filter by minimum USDC threshold
    3. Analyze the wallet(s)
    4. Send alert if new whale detected
    """
    args = event.get("args", {})
    maker = args.get("maker", "")
    taker = args.get("taker", "")
    maker_amount = args.get("makerAmountFilled", 0)
    taker_amount = args.get("takerAmountFilled", 0)
    maker_asset_id = args.get("makerAssetId", 0)
    taker_asset_id = args.get("takerAssetId", 0)
    tx_hash = event.get("transactionHash", b"").hex() if event.get("transactionHash") else ""

    # Determine which side is USDC (collateral) and which is the CTF token.
    # assetId == 0 means collateral (USDC). The other side is the CTF token.
    # BUY: maker sends USDC (makerAssetId=0), taker sends CTF tokens
    # SELL: taker sends USDC (takerAssetId=0), maker sends CTF tokens
    usdc_amount_raw = 0
    ctf_token_id = ""
    side = "BUY"

    if _is_collateral_side(maker_asset_id):
        # Maker is the buyer — sending USDC, receiving CTF tokens
        usdc_amount_raw = maker_amount
        ctf_token_id = str(taker_asset_id)
        side = "BUY"
    elif _is_collateral_side(taker_asset_id):
        # Maker is the seller — sending CTF tokens, receiving USDC
        usdc_amount_raw = taker_amount
        ctf_token_id = str(maker_asset_id)
        side = "SELL"
    else:
        # Both assetIds are non-zero — shouldn't happen, skip
        logger.debug(f"Skipping event with no collateral side: maker={maker_asset_id}, taker={taker_asset_id}")
        return

    usdc_amount = _get_usdc_amount(usdc_amount_raw)

    # Filter: skip trades below threshold
    if usdc_amount < config.min_trade_usdc:
        return

    logger.info(
        f"Large trade detected: ${usdc_amount:,.2f} USDC | "
        f"maker={maker[:10]}... taker={taker[:10]}... | "
        f"{exchange_name}"
    )

    # Check both maker and taker
    for wallet, wallet_side in [(maker, side), (taker, "SELL" if side == "BUY" else "BUY")]:
        if not wallet or wallet == "0x" + "0" * 40:
            continue

        # Skip known Polymarket infrastructure contracts
        if wallet.lower() in KNOWN_CONTRACTS:
            continue

        # Rate limit: don't recheck same wallet too quickly
        now = time.time()
        wallet_lower = wallet.lower()
        if wallet_lower in _recent_checks and now - _recent_checks[wallet_lower] < CHECK_COOLDOWN:
            continue
        _recent_checks[wallet_lower] = now

        # Analyze the wallet
        analysis = await analyze_wallet(wallet, http_client)

        if analysis.is_new:
            # Resolve the market name and current price
            market_info = await resolve_market(ctf_token_id, http_client)
            entry_price = await get_current_price(ctf_token_id, http_client)

            # Skip high-probability bets — not suspicious, just safe money
            if entry_price is not None:
                effective_prob = entry_price if wallet_side == "BUY" else (1.0 - entry_price)
                if effective_prob > 0.85:
                    logger.debug(
                        f"Skipping {wallet[:10]}... — {effective_prob:.0%} implied prob, not suspicious"
                    )
                    continue

            # Check for cluster behavior (other new wallets on same outcome)
            cluster_count = await count_recent_new_wallets_on_outcome(
                ctf_token_id, wallet
            )

            # Calculate suspicion score
            score = score_trade(
                account_age_days=analysis.account_age_days,
                total_trades=analysis.total_trades,
                total_volume_usdc=analysis.total_volume_usdc,
                trade_size_usdc=usdc_amount,
                entry_price=entry_price,
                side=wallet_side,
                unique_markets=analysis.unique_markets,
                cluster_count=cluster_count,
            )

            logger.info(
                f"🚨 NEW WHALE: {wallet[:10]}... | "
                f"${usdc_amount:,.2f} | "
                f"{analysis.total_trades} prior trades | "
                f"{analysis.account_age_days} days old | "
                f"Score: {score.total} ({score.tier}) | "
                f"{market_info.get('title', '?')[:40]}"
            )

            # Only send Telegram alerts for HIGH suspicion scores
            if score.tier == "HIGH":
                await send_whale_alert(
                    analysis=analysis,
                    trade_size_usdc=usdc_amount,
                    market_info=market_info,
                    side=wallet_side,
                    tx_hash=tx_hash,
                    exchange=exchange_name,
                    score=score,
                    entry_price=entry_price,
                )
            else:
                logger.info(f"Score {score.total} ({score.tier}) — skipping Telegram alert")

            await save_signal({
                "wallet": wallet,
                "trade_size_usdc": usdc_amount,
                "side": wallet_side,
                "ctf_token_id": ctf_token_id,
                "market_title": market_info.get("title", ""),
                "outcome": market_info.get("outcome", ""),
                "exchange": exchange_name,
                "tx_hash": tx_hash,
                "account_age_days": analysis.account_age_days,
                "total_trades": analysis.total_trades,
                "total_volume_usdc": analysis.total_volume_usdc,
                "entry_price": entry_price,
                "pseudonym": analysis.pseudonym,
                "condition_id": market_info.get("condition_id", ""),
                "market_slug": market_info.get("slug", ""),
                "suspicion_score": score.total,
                "score_tier": score.tier,
                "score_breakdown": json.dumps(score.components),
                "unique_markets": analysis.unique_markets,
            })


async def poll_events(w3: Web3, http_client: httpx.AsyncClient):
    """Poll for new OrderFilled events using HTTP RPC (fallback mode).

    This is used when WebSocket connections are not available or fail.
    """
    global _last_block

    ctf_exchange = w3.eth.contract(
        address=Web3.to_checksum_address(CTF_EXCHANGE),
        abi=EXCHANGE_ABI,
    )
    neg_risk_exchange = w3.eth.contract(
        address=Web3.to_checksum_address(NEG_RISK_CTF_EXCHANGE),
        abi=EXCHANGE_ABI,
    )

    if _last_block == 0:
        _last_block = w3.eth.block_number - 10  # Start 10 blocks back

    logger.info(f"Polling mode started from block {_last_block}")
    await send_status_message(
        "🟢 <b>Polymarket Whale Bot started</b> (polling mode)\n"
        f"Watching for trades ≥ ${config.min_trade_usdc:,.0f}\n"
        f"New account threshold: ≤{config.max_prior_trades} trades or ≤{config.max_account_age_days} days"
    )

    while True:
        try:
            current_block = w3.eth.block_number

            if current_block <= _last_block:
                await asyncio.sleep(config.polling_interval)
                continue

            # Don't scan too many blocks at once (limit to 100)
            from_block = _last_block + 1
            to_block = min(current_block, from_block + 100)

            # Fetch OrderFilled events from both exchanges
            for contract, name in [
                (ctf_exchange, "CTF Exchange"),
                (neg_risk_exchange, "NegRisk CTF Exchange"),
            ]:
                try:
                    events = contract.events.OrderFilled.get_logs(
                        from_block=from_block,
                        to_block=to_block,
                    )
                    for event in events:
                        await process_order_filled(event, name, http_client)
                except Exception as e:
                    logger.error(f"Error fetching events from {name}: {e}")

            _last_block = to_block
            logger.debug(f"Processed blocks {from_block}-{to_block}")

        except Exception as e:
            logger.error(f"Polling error: {e}")
            await asyncio.sleep(10)

        await asyncio.sleep(config.polling_interval)


async def subscribe_events(w3_ws: Web3, http_client: httpx.AsyncClient):
    """Subscribe to OrderFilled events via WebSocket (preferred mode).

    Uses eth_subscribe to get real-time event notifications.
    Falls back to polling if the WebSocket connection drops.
    """
    ctf_exchange = w3_ws.eth.contract(
        address=Web3.to_checksum_address(CTF_EXCHANGE),
        abi=EXCHANGE_ABI,
    )
    neg_risk_exchange = w3_ws.eth.contract(
        address=Web3.to_checksum_address(NEG_RISK_CTF_EXCHANGE),
        abi=EXCHANGE_ABI,
    )

    logger.info("WebSocket subscription mode starting...")
    await send_status_message(
        "🟢 <b>Polymarket Whale Bot started</b> (websocket mode)\n"
        f"Watching for trades ≥ ${config.min_trade_usdc:,.0f}\n"
        f"New account threshold: ≤{config.max_prior_trades} trades or ≤{config.max_account_age_days} days"
    )

    # Use log filters for both contracts
    # OrderFilled topic hash
    order_filled_topic = w3_ws.keccak(
        text="OrderFilled(bytes32,address,address,uint256,uint256,uint256,uint256,uint256)"
    )

    filter_params = {
        "address": [
            Web3.to_checksum_address(CTF_EXCHANGE),
            Web3.to_checksum_address(NEG_RISK_CTF_EXCHANGE),
        ],
        "topics": [order_filled_topic.hex()],
    }

    try:
        log_filter = w3_ws.eth.filter(filter_params)

        while True:
            try:
                entries = log_filter.get_new_entries()
                for entry in entries:
                    # Determine which exchange this came from
                    addr = entry.get("address", "").lower()
                    if addr == CTF_EXCHANGE.lower():
                        exchange_name = "CTF Exchange"
                        contract = ctf_exchange
                    else:
                        exchange_name = "NegRisk CTF Exchange"
                        contract = neg_risk_exchange

                    # Decode the event
                    try:
                        decoded = contract.events.OrderFilled().process_log(entry)
                        await process_order_filled(decoded, exchange_name, http_client)
                    except Exception as e:
                        logger.warning(f"Failed to decode event: {e}")

            except Exception as e:
                logger.error(f"WebSocket filter error: {e}")
                raise  # Let the caller handle reconnection

            await asyncio.sleep(1)

    except Exception as e:
        logger.warning(f"WebSocket subscription failed: {e}")
        raise


async def run_monitor():
    """Main entry point for the monitor.

    Tries WebSocket mode first, falls back to HTTP polling.
    """
    http_client = httpx.AsyncClient(
        headers={"Accept": "application/json"},
        follow_redirects=True,
    )

    # Try WebSocket mode first
    if config.polygon_ws_rpc:
        try:
            w3_ws = Web3(Web3.WebSocketProvider(config.polygon_ws_rpc))
            if w3_ws.is_connected():
                logger.info("Connected to Polygon via WebSocket")
                try:
                    await subscribe_events(w3_ws, http_client)
                except Exception as e:
                    logger.warning(f"WebSocket mode failed, falling back to polling: {e}")
        except Exception as e:
            logger.warning(f"Could not connect via WebSocket: {e}")

    # Fall back to HTTP polling
    if config.polygon_http_rpc:
        w3 = Web3(Web3.HTTPProvider(config.polygon_http_rpc))
        if w3.is_connected():
            logger.info("Connected to Polygon via HTTP")
            await poll_events(w3, http_client)
        else:
            logger.error("Failed to connect to Polygon via HTTP")
    elif config.polygon_ws_rpc:
        # Try WS provider with polling as fallback
        w3 = Web3(Web3.WebSocketProvider(config.polygon_ws_rpc))
        if w3.is_connected():
            logger.info("Using WebSocket provider in polling mode")
            await poll_events(w3, http_client)

    logger.error("No working RPC connection available")
    await http_client.aclose()
