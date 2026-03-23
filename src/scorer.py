"""Score whale trades on how suspicious/insider-like they are.

Based on patterns from documented insider trading cases on Polymarket:
- Venezuela/Maduro capture, Iran strikes, OpenAI launches,
  Google Year in Search, ZachXBT/Axiom, Israeli military intel leak.

Common threads: brand-new accounts, low-probability bets, concentrated
positions in a single domain, large sizes, cluster behavior.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ScoreBreakdown:
    """Detailed breakdown of how the suspicion score was calculated."""

    total: int = 0
    components: dict[str, int] = field(default_factory=dict)

    def add(self, label: str, points: int):
        self.components[label] = points
        self.total += points

    @property
    def tier(self) -> str:
        if self.total >= 60:
            return "HIGH"
        elif self.total >= 30:
            return "MEDIUM"
        return "LOW"

    @property
    def tier_emoji(self) -> str:
        if self.total >= 60:
            return "🔴"
        elif self.total >= 30:
            return "🟡"
        return "🟢"

    def summary(self) -> str:
        """One-line summary for logging."""
        parts = [f"{k}={v}" for k, v in self.components.items() if v > 0]
        return f"{self.total}/100 ({self.tier}) [{', '.join(parts)}]"


def score_trade(
    account_age_days: float,
    total_trades: int,
    total_volume_usdc: float,
    trade_size_usdc: float,
    entry_price: float | None,
    side: str,
    unique_markets: int,
    cluster_count: int = 0,
    reputation_win_streak: int = 0,
    reputation_total_signals: int = 0,
) -> ScoreBreakdown:
    """Calculate a suspicion score (0-100) for a whale trade.

    Args:
        account_age_days: How old the wallet is.
        total_trades: Number of prior trades on the account.
        total_volume_usdc: Total prior trading volume.
        trade_size_usdc: Size of this specific trade.
        entry_price: Current market price (0.0-1.0), proxy for implied probability.
        side: "BUY" or "SELL".
        unique_markets: Number of distinct markets this wallet has traded.
        cluster_count: Number of OTHER new wallets that bet on the same
                       market outcome in the last 24 hours.
    """
    breakdown = ScoreBreakdown()

    # --- 1. Account age (max 25 pts) ---
    # Brand new accounts are the #1 signal across every documented case.
    if account_age_days <= 1:
        breakdown.add("age", 25)
    elif account_age_days <= 3:
        breakdown.add("age", 20)
    elif account_age_days <= 7:
        breakdown.add("age", 15)
    elif account_age_days <= 14:
        breakdown.add("age", 5)

    # --- 2. Low-probability bet (max 25 pts) ---
    # Insiders bet on outcomes the market prices at <20%.
    # For BUY side, low entry_price = low-probability outcome.
    # For SELL side, high entry_price = they're selling a likely outcome
    # (betting it WON'T happen), so 1-price is the "improbable" direction.
    if entry_price is not None:
        effective_prob = entry_price if side == "BUY" else (1.0 - entry_price)
        if effective_prob <= 0.10:
            breakdown.add("low_prob", 25)
        elif effective_prob <= 0.20:
            breakdown.add("low_prob", 20)
        elif effective_prob <= 0.30:
            breakdown.add("low_prob", 10)

    # --- 3. Trade size (max 15 pts) ---
    # Insiders go big — $20K-$300K+ on single outcomes.
    if trade_size_usdc >= 100_000:
        breakdown.add("size", 15)
    elif trade_size_usdc >= 50_000:
        breakdown.add("size", 12)
    elif trade_size_usdc >= 25_000:
        breakdown.add("size", 10)
    elif trade_size_usdc >= 10_000:
        breakdown.add("size", 7)
    elif trade_size_usdc >= 5_000:
        breakdown.add("size", 3)

    # --- 4. Market concentration (max 15 pts) ---
    # Insiders only trade in 1-4 markets, all in the same domain.
    # OpenAI case: only product launch bets. Venezuela case: only 4 outcomes.
    if unique_markets <= 1:
        breakdown.add("concentration", 15)
    elif unique_markets <= 3:
        breakdown.add("concentration", 12)
    elif unique_markets <= 5:
        breakdown.add("concentration", 7)

    # --- 5. Size vs. history ratio (max 10 pts) ---
    # A brand new wallet dropping $50K when it has $0 prior volume is
    # very different from an established trader making a $50K bet.
    if total_volume_usdc > 0:
        ratio = trade_size_usdc / total_volume_usdc
        if ratio >= 5.0:
            breakdown.add("size_ratio", 10)
        elif ratio >= 2.0:
            breakdown.add("size_ratio", 7)
        elif ratio >= 1.0:
            breakdown.add("size_ratio", 3)
    elif total_trades == 0:
        # First ever trade — max suspicion on this factor
        breakdown.add("size_ratio", 10)

    # --- 6. Cluster behavior (max 10 pts) ---
    # Iran strikes: 6 wallets. OpenAI: 13 wallets. Axiom: 12 wallets.
    # Multiple new wallets piling into the same outcome = coordinated.
    if cluster_count >= 5:
        breakdown.add("cluster", 10)
    elif cluster_count >= 3:
        breakdown.add("cluster", 7)
    elif cluster_count >= 1:
        breakdown.add("cluster", 4)

    # --- 7. Repeat offender bonus (max 15 pts) ---
    # AlphaRaccoon: 22/23 wins. IDF: 7/7. Repeat accuracy = insider.
    if reputation_win_streak >= 5:
        breakdown.add("repeat_winner", 15)
    elif reputation_win_streak >= 3:
        breakdown.add("repeat_winner", 10)
    elif reputation_win_streak >= 2:
        breakdown.add("repeat_winner", 5)

    # Multi-signal wallet that keeps showing up in whale detection
    if reputation_total_signals >= 5:
        breakdown.add("repeat_offender", 5)
    elif reputation_total_signals >= 3:
        breakdown.add("repeat_offender", 3)

    # Cap at 100
    if breakdown.total > 100:
        breakdown.total = 100

    logger.info(f"Suspicion score: {breakdown.summary()}")
    return breakdown
