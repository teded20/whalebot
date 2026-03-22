"""Analyze Polymarket accounts to determine if they are 'new'.

Checks a wallet's trade history via the Polymarket Data API and
determines if the account qualifies as a 'new whale' based on
configurable thresholds.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from .config import config

logger = logging.getLogger(__name__)

DATA_API = "https://data-api.polymarket.com"

# Cache analyzed wallets to avoid re-querying within a session.
_analysis_cache: dict[str, "AnalysisResult"] = {}
CACHE_TTL = 300  # 5 minutes


@dataclass
class AnalysisResult:
    """Result of analyzing a wallet's history."""

    wallet: str
    is_new: bool
    total_trades: int
    first_trade_timestamp: Optional[int]
    account_age_days: float
    total_volume_usdc: float
    pseudonym: Optional[str]
    unique_markets: int  # distinct markets traded
    timestamp: float  # when this analysis was done

    @property
    def profile_url(self) -> str:
        return f"https://polymarket.com/profile/{self.wallet}"

    @property
    def polygonscan_url(self) -> str:
        return f"https://polygonscan.com/address/{self.wallet}"


async def analyze_wallet(
    wallet: str, client: httpx.AsyncClient
) -> AnalysisResult:
    """Check if a wallet is a 'new' account on Polymarket.

    A wallet is considered 'new' if:
    - It has fewer than MAX_PRIOR_TRADES total trades, OR
    - Its first trade was less than MAX_ACCOUNT_AGE_DAYS ago
    """
    wallet_lower = wallet.lower()
    now = time.time()

    # Return cached result if fresh
    if wallet_lower in _analysis_cache:
        cached = _analysis_cache[wallet_lower]
        if now - cached.timestamp < CACHE_TTL:
            return cached

    total_trades = 0
    first_trade_ts: Optional[int] = None
    total_volume = 0.0
    pseudonym = None
    seen_markets: set[str] = set()

    try:
        # Fetch trade history sorted by timestamp ASC to find first trade
        resp = await client.get(
            f"{DATA_API}/activity",
            params={
                "user": wallet,
                "type": "TRADE",
                "sortBy": "TIMESTAMP",
                "sortDirection": "ASC",
                "limit": 100,
            },
            timeout=15,
        )

        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                total_trades = len(data)
                if total_trades > 0:
                    first_trade_ts = data[0].get("timestamp")
                    pseudonym = data[0].get("pseudonym") or data[0].get("name")

                    for trade in data:
                        usdc_size = trade.get("usdcSize", 0)
                        if usdc_size:
                            total_volume += float(usdc_size)
                        # Track unique markets for concentration scoring
                        slug = trade.get("slug") or trade.get("marketSlug") or trade.get("conditionId", "")
                        if slug:
                            seen_markets.add(slug)

    except Exception as e:
        logger.error(f"Failed to analyze wallet {wallet}: {e}")
        result = AnalysisResult(
            wallet=wallet,
            is_new=False,
            total_trades=-1,
            first_trade_timestamp=None,
            account_age_days=-1,
            total_volume_usdc=0,
            pseudonym=None,
            unique_markets=0,
            timestamp=now,
        )
        _analysis_cache[wallet_lower] = result
        return result

    # Calculate account age
    account_age_days = 0.0
    if first_trade_ts:
        account_age_days = (now - first_trade_ts) / 86400

    # The API caps results at 100, so total_trades == 100 means "100+"
    # — treat those as established accounts unless they're very young.
    # Both conditions must hold: few trades AND young account.
    if total_trades >= 100:
        # Hit the API limit — can't determine true trade count.
        # Only flag if the account is extremely young (likely a fresh wallet
        # that rapidly traded to hit the cap).
        is_new = account_age_days <= 1
    else:
        is_new = (
            total_trades <= config.max_prior_trades
            and account_age_days <= config.max_account_age_days
        )

    result = AnalysisResult(
        wallet=wallet,
        is_new=is_new,
        total_trades=total_trades,
        first_trade_timestamp=first_trade_ts,
        account_age_days=round(account_age_days, 1),
        total_volume_usdc=round(total_volume, 2),
        pseudonym=pseudonym,
        unique_markets=len(seen_markets),
        timestamp=now,
    )

    _analysis_cache[wallet_lower] = result
    return result
