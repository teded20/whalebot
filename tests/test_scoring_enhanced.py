"""Test enhanced scoring with all new factors."""
from src.scorer import score_trade


def test_venezuela_pattern():
    """Brand new wallet, low prob, big bet, single market, imminent."""
    score = score_trade(
        account_age_days=0.5,
        total_trades=0,
        total_volume_usdc=0,
        trade_size_usdc=34000,
        entry_price=0.08,
        side="BUY",
        unique_markets=1,
        cluster_count=0,
        reputation_win_streak=0,
        reputation_total_signals=1,
        hours_to_resolution=1,
    )
    assert score.total >= 90, f"Venezuela pattern should score 90+, got {score.total}"
    assert score.tier == "HIGH"


def test_alpharaccoon_pattern():
    """Repeat winner with crazy win streak."""
    score = score_trade(
        account_age_days=30,
        total_trades=25,
        total_volume_usdc=3_000_000,
        trade_size_usdc=200_000,
        entry_price=0.05,
        side="BUY",
        unique_markets=3,
        cluster_count=0,
        reputation_win_streak=10,
        reputation_total_signals=22,
        hours_to_resolution=24,
        is_round_funding=True,
    )
    assert score.total >= 70, f"AlphaRaccoon pattern should score 70+, got {score.total}"
    assert score.components.get("repeat_winner", 0) > 0


def test_iran_wave_pattern():
    """6 fresh wallets, shared funding, coordinated."""
    score = score_trade(
        account_age_days=1,
        total_trades=0,
        total_volume_usdc=0,
        trade_size_usdc=60000,
        entry_price=0.10,
        side="BUY",
        unique_markets=1,
        cluster_count=5,
        reputation_win_streak=0,
        reputation_total_signals=1,
        hours_to_resolution=2,
        shared_funding_source_count=5,
        is_round_funding=True,
    )
    assert score.total >= 90, f"Iran wave pattern should score 90+, got {score.total}"


def test_normal_whale_scores_low():
    """Established trader, mid-prob bet, no red flags."""
    score = score_trade(
        account_age_days=180,
        total_trades=500,
        total_volume_usdc=5_000_000,
        trade_size_usdc=50000,
        entry_price=0.65,
        side="BUY",
        unique_markets=50,
        cluster_count=0,
        reputation_win_streak=1,
        reputation_total_signals=3,
        hours_to_resolution=720,
    )
    assert score.total <= 25, f"Normal whale should score ≤25, got {score.total}"
    assert score.tier == "LOW"
