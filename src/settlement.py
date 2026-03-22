"""Check unresolved signals against Polymarket for settlement.

Queries the Gamma API for each unresolved signal's market,
checks if it has resolved, and updates the DB accordingly.
"""

import asyncio
import json
import logging

import httpx

from .database import get_unresolved_signals, update_signal_resolution

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


async def check_settlements():
    """Check all unresolved signals and update any that have settled.

    Returns (checked, resolved, wins, losses) counts.
    """
    unresolved = await get_unresolved_signals()
    if not unresolved:
        logger.info("No unresolved signals to check")
        return 0, 0, 0, 0

    logger.info(f"Checking {len(unresolved)} unresolved signals...")

    # Deduplicate by condition_id to minimize API calls
    condition_cache: dict[str, dict] = {}
    checked = 0
    resolved = 0
    wins = 0
    losses = 0

    async with httpx.AsyncClient(
        headers={"Accept": "application/json"},
        follow_redirects=True,
        timeout=15,
    ) as client:
        for signal in unresolved:
            checked += 1
            token_id = signal["ctf_token_id"]
            condition_id = signal.get("condition_id", "")

            # Use cached market data if we already fetched this condition
            cache_key = condition_id or token_id
            if cache_key not in condition_cache:
                try:
                    resp = await client.get(
                        f"{GAMMA_API}/markets",
                        params={"clob_token_ids": token_id},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        markets = resp.json()
                        if markets:
                            m = markets[0]
                            # Gamma API uses closed/acceptingOrders/outcomePrices, NOT resolved/winningOutcome
                            raw_outcomes = m.get("outcomes", "[]")
                            raw_prices = m.get("outcomePrices", "[]")
                            outcomes = json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else raw_outcomes
                            prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices

                            is_closed = m.get("closed", False)
                            not_accepting = not m.get("acceptingOrders", True)
                            is_resolved = is_closed and not_accepting

                            winning_outcome = ""
                            if is_resolved:
                                for i, price in enumerate(prices):
                                    if price == "1" and i < len(outcomes):
                                        winning_outcome = outcomes[i]
                                        break
                                # Only mark resolved if we found a clear winner
                                if not winning_outcome:
                                    is_resolved = False

                            condition_cache[cache_key] = {
                                "resolved": is_resolved,
                                "winning_outcome": winning_outcome,
                            }
                except Exception as e:
                    logger.warning(f"Failed to check market for signal {signal['id']}: {e}")
                    continue

                # Rate limit
                await asyncio.sleep(0.2)

            market = condition_cache.get(cache_key)
            if not market or not market["resolved"]:
                continue

            # Market resolved — determine if whale won
            winning_outcome = market["winning_outcome"]
            whale_outcome = signal["outcome"]
            whale_side = signal["side"]

            # BUY Yes + winner=Yes → win. SELL Yes + winner=Yes → loss.
            if whale_side == "BUY":
                won = whale_outcome == winning_outcome
            else:
                # SELL means betting AGAINST this outcome
                won = whale_outcome != winning_outcome

            await update_signal_resolution(signal["id"], won=won, winning_outcome=winning_outcome)
            resolved += 1
            if won:
                wins += 1
            else:
                losses += 1

    logger.info(
        f"Settlement check complete: {checked} checked, {resolved} resolved "
        f"({wins} wins, {losses} losses)"
    )
    return checked, resolved, wins, losses
