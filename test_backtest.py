"""Historical backtest: scan recent chain history for large trades by new wallets,
check if their markets resolved, and calculate win rates at various thresholds.

Uses the public polygon.drpc.org RPC and Polymarket APIs.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

import httpx
from web3 import Web3

from src.contracts import (
    CTF_EXCHANGE,
    NEG_RISK_CTF_EXCHANGE,
    EXCHANGE_ABI,
    USDC_DECIMALS,
    KNOWN_CONTRACTS,
)

RPC_URL = "https://polygon.drpc.org"
GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com"

# Scan last N blocks (~2s per block on Polygon)
SCAN_BLOCKS = 5000  # ~2.8 hours
# Minimum trade to even consider (raw filter before threshold analysis)
MIN_SCAN_USDC = 500

THRESHOLDS = [500, 1_000, 2_500, 5_000, 10_000, 25_000, 50_000]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("web3").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logger = logging.getLogger("backtest")


@dataclass
class Trade:
    wallet: str
    usdc_amount: float
    side: str  # BUY or SELL
    ctf_token_id: str
    tx_hash: str
    block: int
    exchange: str
    # Filled in later
    wallet_trades: int = -1
    wallet_age_days: float = -1
    market_title: str = ""
    outcome_picked: str = ""  # which outcome the whale bet on
    market_resolved: bool = False
    winning_outcome: str = ""  # what actually won
    whale_won: bool | None = None  # True/False/None(pending)


@dataclass
class ThresholdStats:
    threshold: float
    total_signals: int = 0
    wins: int = 0
    losses: int = 0
    pending: int = 0
    total_usdc: float = 0

    @property
    def resolved(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> str:
        if self.resolved == 0:
            return "N/A"
        return f"{self.wins / self.resolved * 100:.0f}%"


def _is_collateral_side(asset_id: int) -> bool:
    return asset_id == 0


async def fetch_events(w3: Web3) -> list[Trade]:
    """Fetch OrderFilled events from recent blocks."""
    latest = w3.eth.block_number
    from_block = latest - SCAN_BLOCKS
    trades: list[Trade] = []

    logger.info(f"Scanning blocks {from_block} - {latest} ({SCAN_BLOCKS} blocks, ~{SCAN_BLOCKS * 2 / 60:.0f} min)")

    for addr, name in [
        (CTF_EXCHANGE, "CTF"),
        (NEG_RISK_CTF_EXCHANGE, "NegRisk"),
    ]:
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(addr), abi=EXCHANGE_ABI
        )

        # Fetch in chunks to avoid RPC limits
        chunk_size = 500
        block = from_block
        event_count = 0

        while block <= latest:
            to_block = min(block + chunk_size - 1, latest)
            try:
                events = contract.events.OrderFilled.get_logs(
                    from_block=block, to_block=to_block
                )
                event_count += len(events)

                for event in events:
                    args = event["args"]
                    maker_asset_id = args["makerAssetId"]
                    taker_asset_id = args["takerAssetId"]

                    if _is_collateral_side(maker_asset_id):
                        usdc_raw = args["makerAmountFilled"]
                        ctf_token_id = str(taker_asset_id)
                        side = "BUY"
                        wallet = args["maker"]
                    elif _is_collateral_side(taker_asset_id):
                        usdc_raw = args["takerAmountFilled"]
                        ctf_token_id = str(maker_asset_id)
                        side = "SELL"
                        wallet = args["maker"]
                    else:
                        continue

                    usdc = usdc_raw / (10 ** USDC_DECIMALS)
                    if usdc < MIN_SCAN_USDC:
                        continue

                    wl = wallet.lower() if isinstance(wallet, str) else wallet.lower()
                    if wl in KNOWN_CONTRACTS:
                        continue

                    trades.append(Trade(
                        wallet=wallet,
                        usdc_amount=usdc,
                        side=side,
                        ctf_token_id=ctf_token_id,
                        tx_hash=event["transactionHash"].hex(),
                        block=event["blockNumber"],
                        exchange=name,
                    ))

            except Exception as e:
                logger.warning(f"Error fetching {name} blocks {block}-{to_block}: {e}")

            block = to_block + 1

        logger.info(f"  {name}: {event_count:,} events, {sum(1 for t in trades if t.exchange == name)} trades >= ${MIN_SCAN_USDC}")

    return trades


async def analyze_wallets(trades: list[Trade], client: httpx.AsyncClient):
    """Check wallet newness for each trade (deduplicated)."""
    wallets_seen: dict[str, dict] = {}
    wallets_to_check = set(t.wallet for t in trades)

    logger.info(f"Analyzing {len(wallets_to_check)} unique wallets...")

    for i, wallet in enumerate(wallets_to_check):
        if i > 0 and i % 20 == 0:
            logger.info(f"  ...checked {i}/{len(wallets_to_check)} wallets")

        try:
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
                    total = len(data)
                    age_days = 0.0
                    if total > 0 and data[0].get("timestamp"):
                        age_days = (time.time() - data[0]["timestamp"]) / 86400
                    wallets_seen[wallet] = {"trades": total, "age_days": round(age_days, 1)}
                else:
                    wallets_seen[wallet] = {"trades": 0, "age_days": 0}
            else:
                wallets_seen[wallet] = {"trades": -1, "age_days": -1}
        except Exception:
            wallets_seen[wallet] = {"trades": -1, "age_days": -1}

        # Rate limit
        await asyncio.sleep(0.15)

    # Fill in trade data
    for t in trades:
        info = wallets_seen.get(t.wallet, {})
        t.wallet_trades = info.get("trades", -1)
        t.wallet_age_days = info.get("age_days", -1)


async def resolve_markets(trades: list[Trade], client: httpx.AsyncClient):
    """Look up market resolution status for each trade."""
    tokens_seen: dict[str, dict] = {}
    unique_tokens = set(t.ctf_token_id for t in trades)

    logger.info(f"Resolving {len(unique_tokens)} unique markets...")

    for i, token_id in enumerate(unique_tokens):
        if i > 0 and i % 20 == 0:
            logger.info(f"  ...resolved {i}/{len(unique_tokens)} markets")

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
                    # Parse token IDs and outcomes
                    raw_tokens = m.get("clobTokenIds", "[]")
                    raw_outcomes = m.get("outcomes", "[]")
                    try:
                        tokens = json.loads(raw_tokens) if isinstance(raw_tokens, str) else raw_tokens
                    except Exception:
                        tokens = []
                    try:
                        outcomes = json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else raw_outcomes
                    except Exception:
                        outcomes = []

                    # Which outcome does this token represent?
                    outcome_name = "Unknown"
                    for j, tok in enumerate(tokens):
                        if tok.strip() == token_id and j < len(outcomes):
                            outcome_name = outcomes[j]
                            break

                    # Resolution status
                    resolved = m.get("resolved", False)
                    winning = m.get("winningOutcome", "") or ""

                    tokens_seen[token_id] = {
                        "title": m.get("question", m.get("title", "?")),
                        "outcome": outcome_name,
                        "resolved": resolved,
                        "winning": winning,
                    }
        except Exception:
            pass

        await asyncio.sleep(0.1)

    # Fill in trade data
    for t in trades:
        info = tokens_seen.get(t.ctf_token_id, {})
        t.market_title = info.get("title", "?")
        t.outcome_picked = info.get("outcome", "?")
        t.market_resolved = info.get("resolved", False)
        t.winning_outcome = info.get("winning", "")

        if t.market_resolved and t.winning_outcome and t.outcome_picked != "Unknown":
            t.whale_won = (t.outcome_picked == t.winning_outcome)


def is_new_wallet(t: Trade) -> bool:
    """Same logic as the live bot."""
    if t.wallet_trades < 0:
        return False
    if t.wallet_trades >= 100:
        return t.wallet_age_days <= 1
    return t.wallet_trades <= 10 and t.wallet_age_days <= 7


def compute_stats(trades: list[Trade]) -> list[ThresholdStats]:
    """Compute win/loss stats at each threshold."""
    # Filter to new-wallet trades only
    new_wallet_trades = [t for t in trades if is_new_wallet(t)]

    stats = []
    for threshold in THRESHOLDS:
        s = ThresholdStats(threshold=threshold)
        seen_wallets: set[str] = set()

        for t in new_wallet_trades:
            if t.usdc_amount < threshold:
                continue
            # Dedupe: count each wallet only once per threshold
            wkey = f"{t.wallet}:{t.ctf_token_id}"
            if wkey in seen_wallets:
                continue
            seen_wallets.add(wkey)

            s.total_signals += 1
            s.total_usdc += t.usdc_amount

            if t.whale_won is True:
                s.wins += 1
            elif t.whale_won is False:
                s.losses += 1
            else:
                s.pending += 1

        stats.append(s)

    return stats


async def main():
    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 15}))
    if not w3.is_connected():
        logger.error("Cannot connect to Polygon RPC")
        return

    client = httpx.AsyncClient(
        headers={"Accept": "application/json"}, follow_redirects=True, timeout=15
    )

    try:
        # Step 1: Fetch events
        trades = await fetch_events(w3)
        logger.info(f"Total large trades found: {len(trades)}")

        if not trades:
            logger.info("No trades found — try increasing SCAN_BLOCKS")
            return

        # Step 2: Analyze wallets
        await analyze_wallets(trades, client)

        new_trades = [t for t in trades if is_new_wallet(t)]
        logger.info(f"Trades by new wallets: {len(new_trades)}")

        if not new_trades:
            logger.info("No new-wallet trades found at any threshold")
            return

        # Step 3: Resolve markets
        await resolve_markets(new_trades, client)

        resolved_count = sum(1 for t in new_trades if t.market_resolved)
        logger.info(f"Markets resolved: {resolved_count}/{len(new_trades)}")

        # Step 4: Compute and print stats
        stats = compute_stats(trades)

        print()
        print("=" * 80)
        print(f"BACKTEST RESULTS — Last {SCAN_BLOCKS} blocks (~{SCAN_BLOCKS * 2 / 60:.0f} min)")
        print(f"New wallet criteria: <=10 trades AND <=7 days (or <=1 day if 100+ trades)")
        print("=" * 80)
        print()
        print(f"{'Threshold':>12} | {'Signals':>8} | {'Resolved':>8} | {'Wins':>6} | {'Losses':>6} | {'Pending':>8} | {'Win Rate':>8} | {'Total USDC':>12}")
        print("-" * 80)

        for s in stats:
            print(
                f"  ${s.threshold:>9,.0f} | {s.total_signals:>8} | {s.resolved:>8} | "
                f"{s.wins:>6} | {s.losses:>6} | {s.pending:>8} | {s.win_rate:>8} | "
                f"${s.total_usdc:>11,.0f}"
            )

        print()

        # Show some example signals
        big_new = sorted(
            [t for t in new_trades if t.usdc_amount >= 1000],
            key=lambda t: -t.usdc_amount,
        )[:10]

        if big_new:
            print("TOP SIGNALS (largest new-wallet trades):")
            print("-" * 80)
            for t in big_new:
                status = "PENDING"
                if t.whale_won is True:
                    status = "WIN"
                elif t.whale_won is False:
                    status = "LOSS"

                print(
                    f"  ${t.usdc_amount:>10,.2f} {t.side:>4} | "
                    f"{t.market_title[:45]:<45} | "
                    f"{t.outcome_picked:<10} | "
                    f"{status:<7} | "
                    f"age={t.wallet_age_days}d trades={t.wallet_trades}"
                )
            print()

    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
