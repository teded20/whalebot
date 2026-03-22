"""End-to-end simulation: poll live chain, analyze wallets, print alerts.

Runs for ~30 seconds with a $1 threshold to catch real events.
Skips Telegram — just prints what would be alerted.
"""

import asyncio
import logging
import time
import json

import httpx
from web3 import Web3

from src.contracts import (
    CTF_EXCHANGE,
    NEG_RISK_CTF_EXCHANGE,
    EXCHANGE_ABI,
    USDC_DECIMALS,
    KNOWN_CONTRACTS,
)
from src.analyzer import analyze_wallet
from src.market_resolver import resolve_market

RPC_URL = "https://polygon.drpc.org"
MIN_TRADE_USDC = 1.0  # Low threshold to catch events during test
RUN_DURATION = 30  # seconds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("web3").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logger = logging.getLogger("e2e")


def _is_collateral_side(asset_id: int) -> bool:
    return asset_id == 0


async def main():
    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 15}))
    if not w3.is_connected():
        logger.error("Cannot connect to Polygon RPC")
        return

    latest = w3.eth.block_number
    logger.info(f"Connected. Latest block: {latest}")

    # Start 50 blocks back to have some events immediately
    last_block = latest - 50

    ctf = w3.eth.contract(
        address=Web3.to_checksum_address(CTF_EXCHANGE), abi=EXCHANGE_ABI
    )
    neg_risk = w3.eth.contract(
        address=Web3.to_checksum_address(NEG_RISK_CTF_EXCHANGE), abi=EXCHANGE_ABI
    )

    client = httpx.AsyncClient(
        headers={"Accept": "application/json"}, follow_redirects=True, timeout=15
    )

    alerts_triggered = 0
    events_processed = 0
    large_trades_found = 0
    contracts_skipped = 0
    established_skipped = 0
    wallets_analyzed = set()
    start_time = time.time()

    logger.info(f"Polling for {RUN_DURATION}s with ${MIN_TRADE_USDC} threshold...")
    logger.info("")

    try:
        while time.time() - start_time < RUN_DURATION:
            current = w3.eth.block_number
            if current <= last_block:
                await asyncio.sleep(2)
                continue

            from_block = last_block + 1
            to_block = min(current, from_block + 50)

            for contract, name in [(ctf, "CTF"), (neg_risk, "NegRisk")]:
                try:
                    events = contract.events.OrderFilled.get_logs(
                        from_block=from_block, to_block=to_block
                    )
                except Exception as e:
                    logger.warning(f"Error fetching from {name}: {e}")
                    continue

                for event in events:
                    events_processed += 1
                    args = event["args"]
                    maker = args["maker"]
                    taker = args["taker"]
                    maker_asset_id = args["makerAssetId"]
                    taker_asset_id = args["takerAssetId"]
                    maker_amount = args["makerAmountFilled"]
                    taker_amount = args["takerAmountFilled"]
                    tx_hash = event["transactionHash"].hex()

                    # Determine USDC side
                    if _is_collateral_side(maker_asset_id):
                        usdc_raw = maker_amount
                        ctf_token_id = str(taker_asset_id)
                        side = "BUY"
                    elif _is_collateral_side(taker_asset_id):
                        usdc_raw = taker_amount
                        ctf_token_id = str(maker_asset_id)
                        side = "SELL"
                    else:
                        continue

                    usdc = usdc_raw / (10**USDC_DECIMALS)
                    if usdc < MIN_TRADE_USDC:
                        continue

                    large_trades_found += 1

                    # Only analyze each wallet once during the test
                    for wallet, w_side in [
                        (maker, side),
                        (taker, "SELL" if side == "BUY" else "BUY"),
                    ]:
                        if not wallet or wallet == "0x" + "0" * 40:
                            continue
                        wl = wallet.lower()
                        if wl in KNOWN_CONTRACTS:
                            contracts_skipped += 1
                            continue
                        if wl in wallets_analyzed:
                            continue
                        wallets_analyzed.add(wl)

                        analysis = await analyze_wallet(wallet, client)
                        if not analysis.is_new:
                            established_skipped += 1
                        if analysis.is_new:
                            alerts_triggered += 1
                            market = await resolve_market(ctf_token_id, client)

                            logger.info("=" * 60)
                            logger.info(f"ALERT #{alerts_triggered} — NEW WHALE DETECTED")
                            logger.info(f"  Wallet:       {wallet}")
                            logger.info(f"  Pseudonym:    {analysis.pseudonym or 'N/A'}")
                            logger.info(f"  Side:         {w_side}")
                            logger.info(f"  Trade size:   ${usdc:,.2f}")
                            logger.info(f"  Prior trades: {analysis.total_trades}")
                            logger.info(f"  Account age:  {analysis.account_age_days} days")
                            logger.info(f"  Total volume: ${analysis.total_volume_usdc:,.2f}")
                            logger.info(f"  Market:       {market.get('title', '?')}")
                            logger.info(f"  Outcome:      {market.get('outcome', '?')}")
                            logger.info(f"  Exchange:     {name}")
                            logger.info(f"  Tx:           https://polygonscan.com/tx/{tx_hash}")
                            logger.info(f"  Profile:      {analysis.profile_url}")
                            logger.info("=" * 60)
                            logger.info("")

            last_block = to_block
            await asyncio.sleep(3)

    except KeyboardInterrupt:
        pass
    finally:
        await client.aclose()

    elapsed = time.time() - start_time
    logger.info("")
    logger.info(f"{'='*60}")
    logger.info(f"E2E TEST COMPLETE ({elapsed:.0f}s)")
    logger.info(f"  Events processed:  {events_processed:,}")
    logger.info(f"  Trades >= ${MIN_TRADE_USDC}:  {large_trades_found:,}")
    logger.info(f"  Wallets analyzed:  {len(wallets_analyzed)}")
    logger.info(f"  Contracts skipped: {contracts_skipped}")
    logger.info(f"  Established skip:  {established_skipped}")
    logger.info(f"  Alerts triggered:  {alerts_triggered}")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
