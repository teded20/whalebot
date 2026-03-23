"""Resolve Polymarket token/asset IDs to human-readable market names.

Uses the Gamma API (public, no auth required) to look up market metadata
and caches results to avoid hammering the API.
"""

import json
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

# In-memory cache: token_id -> market info dict
_cache: dict[str, dict] = {}


async def resolve_market(token_id: str, client: httpx.AsyncClient) -> dict:
    """Look up market info for a given CTF token ID.

    Returns a dict with keys: title, slug, outcome, event_slug, icon
    """
    if token_id in _cache:
        return _cache[token_id]

    info = {
        "title": "Unknown market",
        "slug": "",
        "outcome": "Unknown",
        "event_slug": "",
        "icon": "",
        "token_id": token_id,
        "condition_id": "",
    }

    try:
        resp = await client.get(
            f"{GAMMA_API}/markets",
            params={"clob_token_ids": token_id},
            timeout=10,
        )
        if resp.status_code == 200:
            markets = resp.json()
            if markets and len(markets) > 0:
                market = markets[0]
                # Both clobTokenIds and outcomes come as JSON-encoded strings
                raw_tokens = market.get("clobTokenIds", "[]")
                raw_outcomes = market.get("outcomes", "[]")
                try:
                    tokens = json.loads(raw_tokens) if isinstance(raw_tokens, str) else raw_tokens
                except Exception:
                    tokens = []
                try:
                    outcomes = json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else raw_outcomes
                except Exception:
                    outcomes = []

                outcome_name = "Unknown"
                for i, t in enumerate(tokens):
                    if t.strip() == token_id and i < len(outcomes):
                        outcome_name = outcomes[i]
                        break

                # Event slug lives inside the events array, not at the top level
                event_slug = ""
                events = market.get("events", [])
                if events and isinstance(events, list):
                    event_slug = events[0].get("slug", "")

                # Get end date from market or parent event
                end_date = market.get("endDate", "")
                if not end_date and events:
                    end_date = events[0].get("endDate", "")

                info = {
                    "title": market.get("question", market.get("title", "Unknown")),
                    "slug": event_slug or market.get("slug", ""),
                    "outcome": outcome_name,
                    "event_slug": event_slug,
                    "icon": market.get("icon", ""),
                    "token_id": token_id,
                    "condition_id": market.get("conditionId", ""),
                    "end_date": end_date,
                }
    except Exception as e:
        logger.warning(f"Failed to resolve market for token {token_id}: {e}")

    _cache[token_id] = info
    return info


async def get_current_price(token_id: str, client: httpx.AsyncClient) -> float | None:
    """Fetch the current best ask price for a CTF token from the CLOB API.

    Returns the price as a float (0.0-1.0) or None if unavailable.
    This represents what you'd pay to buy the outcome RIGHT NOW.
    """
    try:
        resp = await client.get(
            f"{CLOB_API}/price",
            params={"token_id": token_id, "side": "BUY"},
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            price = data.get("price")
            if price is not None:
                return float(price)
    except Exception as e:
        logger.debug(f"Failed to fetch price for {token_id}: {e}")
    return None


async def get_profile(wallet: str, client: httpx.AsyncClient) -> Optional[dict]:
    """Fetch public profile info for a wallet address."""
    try:
        resp = await client.get(f"{DATA_API}/profile/{wallet}", timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.warning(f"Failed to fetch profile for {wallet}: {e}")
    return None
