# Insider Detection Enhancements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dramatically improve insider trading detection quality by adding wallet reputation tracking, funding source analysis, time-to-resolution scoring, and coordinated wave detection — based on patterns from every documented Polymarket/Kalshi insider case.

**Architecture:** Four independent subsystems that each add a new scoring dimension. Each phase adds a DB table, a scoring component, and dashboard visibility. The existing `score_trade()` function gains new optional parameters; existing signals are unaffected. New data is collected opportunistically during the existing `process_order_filled()` flow.

**Tech Stack:** Python 3.11+ (asyncio, httpx, psycopg), PostgreSQL/Neon, Next.js (dashboard), Polygon RPC (web3), Polymarket Gamma/CLOB/Data APIs

**DB Pattern Note:** The existing codebase uses `_pool` (not `pool`) for the connection pool in `database.py`, and every write function calls `await conn.commit()` after executing. All new database functions in this plan must follow this pattern: use `_pool.connection()`, guard with `if _pool is None: raise`, and `await conn.commit()` after writes.

---

## Phase 1: Wallet Reputation System

Track wallets across signals, compute rolling win rates, and flag repeat winners with statistically improbable records. This catches the AlphaRaccoon (22/23 wins) and IDF reservist (7/7) patterns.

### Task 1.1: Create wallet_reputation table

**Files:**
- Modify: `src/database.py` (add table creation in `init_db()`, add query functions)

- [ ] **Step 1: Add wallet_reputation table to schema**

Add to `init_db()` before the `await conn.commit()` at the end of the function (around line 99):

```python
await conn.execute("""
    CREATE TABLE IF NOT EXISTS wallet_reputation (
        wallet TEXT PRIMARY KEY,
        first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        total_signals INT NOT NULL DEFAULT 0,
        total_resolved INT NOT NULL DEFAULT 0,
        total_wins INT NOT NULL DEFAULT 0,
        total_losses INT NOT NULL DEFAULT 0,
        total_volume_usdc DOUBLE PRECISION NOT NULL DEFAULT 0,
        avg_entry_probability DOUBLE PRECISION,
        markets_traded TEXT[] NOT NULL DEFAULT '{}',
        last_signal_at TIMESTAMPTZ,
        suspicion_streak INT NOT NULL DEFAULT 0,
        highest_score INT NOT NULL DEFAULT 0,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_wallet_rep_wins
        ON wallet_reputation(total_wins);
    CREATE INDEX IF NOT EXISTS idx_wallet_rep_streak
        ON wallet_reputation(suspicion_streak);
""")
```

- [ ] **Step 2: Add upsert function for wallet reputation**

Add to `database.py`:

```python
async def upsert_wallet_reputation(
    wallet: str,
    trade_size_usdc: float,
    entry_price: float | None,
    suspicion_score: int,
    condition_id: str,
) -> dict:
    """Update wallet reputation after a new signal. Returns current reputation."""
    async with _pool.connection() as conn:
        row = await conn.execute("""
            INSERT INTO wallet_reputation (
                wallet, first_seen, total_signals, total_volume_usdc,
                markets_traded, last_signal_at, highest_score, updated_at
            ) VALUES (
                %(wallet)s, NOW(), 1, %(volume)s,
                ARRAY[%(cid)s], NOW(), %(score)s, NOW()
            )
            ON CONFLICT (wallet) DO UPDATE SET
                total_signals = wallet_reputation.total_signals + 1,
                total_volume_usdc = wallet_reputation.total_volume_usdc + %(volume)s,
                markets_traded = (
                    SELECT array_agg(DISTINCT m)
                    FROM unnest(wallet_reputation.markets_traded || ARRAY[%(cid)s]) AS m
                ),
                last_signal_at = NOW(),
                highest_score = GREATEST(wallet_reputation.highest_score, %(score)s),
                updated_at = NOW()
            RETURNING *
        """, {
            "wallet": wallet.lower(),
            "volume": trade_size_usdc,
            "cid": condition_id,
            "score": suspicion_score,
        })
        return dict(await row.fetchone())
```

- [ ] **Step 3: Add function to update reputation on settlement**

Add to `database.py`:

```python
async def update_wallet_reputation_on_resolution(
    wallet: str, won: bool
) -> None:
    """Update wallet reputation when a signal resolves."""
    async with _pool.connection() as conn:
        await conn.execute("""
            UPDATE wallet_reputation SET
                total_resolved = total_resolved + 1,
                total_wins = total_wins + CASE WHEN %(won)s THEN 1 ELSE 0 END,
                total_losses = total_losses + CASE WHEN %(won)s THEN 0 ELSE 1 END,
                suspicion_streak = CASE
                    WHEN %(won)s THEN suspicion_streak + 1
                    ELSE 0
                END,
                updated_at = NOW()
            WHERE wallet = %(wallet)s
        """, {"wallet": wallet.lower(), "won": won})
```

- [ ] **Step 4: Add function to get wallet reputation**

Add to `database.py`:

```python
async def get_wallet_reputation(wallet: str) -> dict | None:
    """Get wallet reputation. Returns None if wallet not seen before."""
    async with _pool.connection() as conn:
        row = await conn.execute("""
            SELECT * FROM wallet_reputation WHERE wallet = %(wallet)s
        """, {"wallet": wallet.lower()})
        result = await row.fetchone()
        return dict(result) if result else None
```

- [ ] **Step 5: Run bot to verify table creation**

Run: `python -c "import asyncio; from src.database import init_db; asyncio.run(init_db())"`
Expected: No errors, table created.

- [ ] **Step 6: Commit**

```bash
git add src/database.py
git commit -m "feat: add wallet_reputation table and query functions"
```

### Task 1.2: Integrate reputation into signal flow

**Files:**
- Modify: `src/monitor.py` (call upsert after saving signal)
- Modify: `src/settlement.py` (call update on resolution)
- Modify: `src/scorer.py` (add reputation_boost parameter)

- [ ] **Step 1: Update monitor to track wallet reputation**

In `src/monitor.py`, after the `await save_signal({...})` block (around line 207), add:

```python
            # Update wallet reputation
            from .database import upsert_wallet_reputation
            reputation = await upsert_wallet_reputation(
                wallet=wallet,
                trade_size_usdc=usdc_amount,
                entry_price=entry_price,
                suspicion_score=score.total,
                condition_id=market_info.get("condition_id", ""),
            )

            # Log repeat offenders
            if reputation["total_signals"] > 1:
                logger.info(
                    f"👀 REPEAT WALLET: {wallet[:10]}... | "
                    f"Signal #{reputation['total_signals']} | "
                    f"Win streak: {reputation['suspicion_streak']} | "
                    f"Wins: {reputation['total_wins']}/{reputation['total_resolved']}"
                )
```

- [ ] **Step 2: Update settlement to update reputation on resolution**

In `src/settlement.py`, after the `await update_signal_resolution(...)` call (around line 109), add:

```python
                from .database import update_wallet_reputation_on_resolution
                await update_wallet_reputation_on_resolution(
                    wallet=sig["wallet"], won=won
                )
```

- [ ] **Step 3: Add reputation bonus to scorer**

In `src/scorer.py`, add a new parameter and scoring section to `score_trade()`:

Add parameter to function signature (line 58):
```python
    reputation_win_streak: int = 0,
    reputation_total_signals: int = 0,
```

Add new scoring section after cluster behavior (after line 146):

```python
    # --- 7. Repeat offender bonus (max 15 pts) ---
    # AlphaRaccoon: 22/23 wins. IDF: 7/7. Repeat accuracy = insider.
    # Only counts if wallet has been seen before with resolved outcomes.
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
```

Update the cap comment — max is now 120 before capping.

- [ ] **Step 4: Pass reputation data into scorer from monitor**

In `src/monitor.py`, the flow needs to be: (1) fetch reputation READ-ONLY first, (2) score with reputation data, (3) upsert reputation with score after. Reorder:

1. Fetch price, check probability filter (existing)
2. **Read** wallet reputation via `get_wallet_reputation()` (read-only, before scoring)
3. Score trade with reputation data (updated call)
4. **Upsert** wallet reputation with the new score (after scoring)
5. Send alert if HIGH (existing)
6. Save signal (existing)

Add before the `score_trade()` call:

```python
            from .database import get_wallet_reputation
            reputation = await get_wallet_reputation(wallet) or {
                "suspicion_streak": 0, "total_signals": 0
            }
```

Then update the `score_trade()` call:

```python
            score = score_trade(
                account_age_days=analysis.account_age_days,
                total_trades=analysis.total_trades,
                total_volume_usdc=analysis.total_volume_usdc,
                trade_size_usdc=usdc_amount,
                entry_price=entry_price,
                side=wallet_side,
                unique_markets=analysis.unique_markets,
                cluster_count=cluster_count,
                reputation_win_streak=reputation.get("suspicion_streak", 0),
                reputation_total_signals=reputation.get("total_signals", 0),
            )
```

- [ ] **Step 5: Commit**

```bash
git add src/monitor.py src/settlement.py src/scorer.py
git commit -m "feat: integrate wallet reputation into scoring pipeline"
```

### Task 1.3: Backfill reputation from existing signals

**Files:**
- Create: `scripts/backfill_reputation.py`

- [ ] **Step 1: Write backfill script**

```python
"""One-time backfill of wallet_reputation from existing signals."""
import asyncio
import os
from dotenv import load_dotenv
import psycopg

load_dotenv()

async def backfill():
    conn = await psycopg.AsyncConnection.connect(os.environ["DATABASE_URL"])
    async with conn:
        # Build reputation from all existing signals
        await conn.execute("""
            INSERT INTO wallet_reputation (
                wallet, first_seen, total_signals, total_resolved,
                total_wins, total_losses, total_volume_usdc,
                markets_traded, last_signal_at, highest_score, updated_at
            )
            SELECT
                LOWER(wallet),
                MIN(created_at),
                COUNT(*),
                COUNT(*) FILTER (WHERE resolved),
                COUNT(*) FILTER (WHERE resolved AND won),
                COUNT(*) FILTER (WHERE resolved AND NOT won),
                SUM(trade_size_usdc),
                array_agg(DISTINCT condition_id) FILTER (WHERE condition_id != ''),
                MAX(created_at),
                MAX(suspicion_score),
                NOW()
            FROM signals
            GROUP BY LOWER(wallet)
            ON CONFLICT (wallet) DO UPDATE SET
                total_signals = EXCLUDED.total_signals,
                total_resolved = EXCLUDED.total_resolved,
                total_wins = EXCLUDED.total_wins,
                total_losses = EXCLUDED.total_losses,
                total_volume_usdc = EXCLUDED.total_volume_usdc,
                markets_traded = EXCLUDED.markets_traded,
                last_signal_at = EXCLUDED.last_signal_at,
                highest_score = EXCLUDED.highest_score,
                updated_at = NOW()
        """)

        # Compute win streaks (simplified — marks current streak from most recent resolved signals)
        rows = await conn.execute("""
            SELECT LOWER(wallet) as wallet, resolved, won, created_at
            FROM signals
            WHERE resolved = true
            ORDER BY LOWER(wallet), created_at DESC
        """)
        streaks = {}
        for row in await rows.fetchall():
            w = row[0]
            if w not in streaks:
                streaks[w] = 0
            won = row[2]
            if won and streaks[w] >= 0:
                streaks[w] += 1
            else:
                streaks[w] = -1  # streak broken, stop counting

        for w, streak in streaks.items():
            if streak > 0:
                await conn.execute(
                    "UPDATE wallet_reputation SET suspicion_streak = %s WHERE wallet = %s",
                    (streak, w),
                )

        await conn.commit()
        count = await conn.execute("SELECT COUNT(*) FROM wallet_reputation")
        total = (await count.fetchone())[0]
        print(f"Backfilled {total} wallet reputations")

asyncio.run(backfill())
```

- [ ] **Step 2: Create directory and run backfill**

Run: `mkdir -p scripts && python scripts/backfill_reputation.py`
Expected: "Backfilled N wallet reputations" with N matching distinct wallet count.

- [ ] **Step 3: Commit**

```bash
git add scripts/backfill_reputation.py
git commit -m "feat: add wallet reputation backfill script"
```

### Task 1.4: Add reputation to dashboard

**Files:**
- Modify: `dashboard/src/app/page.tsx` (add repeat-wallet indicator to signal rows)
- Modify: `dashboard/src/lib/types.ts` (add reputation fields)

- [ ] **Step 1: Update Signal type**

In `dashboard/src/lib/types.ts`, add to the Signal interface:

```typescript
  // Wallet reputation (joined from wallet_reputation table)
  wallet_signal_count?: number;
  wallet_win_streak?: number;
  wallet_win_rate?: number;
```

- [ ] **Step 2: Update the recent signals query**

In `dashboard/src/app/page.tsx`, update the recent signals SQL query to join with wallet_reputation:

```sql
SELECT s.id, s.created_at, s.wallet, s.trade_size_usdc, s.side,
       s.market_title, s.outcome, s.account_age_days, s.total_trades,
       s.entry_price, s.resolved, s.won, s.winning_outcome, s.market_slug,
       s.suspicion_score, s.score_tier, s.score_breakdown, s.unique_markets,
       wr.total_signals as wallet_signal_count,
       wr.suspicion_streak as wallet_win_streak,
       CASE WHEN wr.total_resolved > 0
            THEN wr.total_wins::float / wr.total_resolved
            ELSE NULL END as wallet_win_rate
FROM signals s
LEFT JOIN wallet_reputation wr ON LOWER(s.wallet) = wr.wallet
${sql.unsafe(clause)}
ORDER BY s.created_at DESC
LIMIT 50
```

**Important:** Update `buildWhereClause()` to prefix ALL column references with `s.`:
- `suspicion_score` → `s.suspicion_score`
- `trade_size_usdc` → `s.trade_size_usdc`
- `account_age_days` → `s.account_age_days`
- `resolved` / `won` → `s.resolved` / `s.won`
- `side` → `s.side`

Only the recent signals query uses the JOIN — the totals/by-threshold/by-age/by-score queries remain unchanged (they don't join wallet_reputation).

- [ ] **Step 3: Add repeat-wallet badge to signal rows**

In the signal table row rendering, after the score column, add a visual indicator for repeat wallets:

```tsx
{s.wallet_signal_count && s.wallet_signal_count > 1 && (
  <span className="ml-1 text-xs px-1 py-0.5 rounded bg-purple-900/50 text-purple-300">
    {s.wallet_signal_count}x
    {s.wallet_win_streak && s.wallet_win_streak >= 2
      ? ` 🔥${s.wallet_win_streak}`
      : ""}
  </span>
)}
```

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/app/page.tsx dashboard/src/lib/types.ts
git commit -m "feat: show wallet reputation badges in dashboard"
```

---

## Phase 2: Time-to-Resolution Scoring

Factor in how close a market is to resolving when the bet is placed. Every documented insider case involved bets placed <72 hours before the event. A $50K bet on a market resolving tomorrow is infinitely more suspicious than the same bet on a market resolving in 6 months.

### Task 2.1: Fetch market end date from Gamma API

**Files:**
- Modify: `src/market_resolver.py` (add end_date to resolved market info)

- [ ] **Step 1: Add end_date to market info dict**

In `resolve_market()`, update the `info` dict construction (around line 77) to include:

```python
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
```

- [ ] **Step 2: Commit**

```bash
git add src/market_resolver.py
git commit -m "feat: extract market end_date from Gamma API"
```

### Task 2.2: Add time-proximity scoring

**Files:**
- Modify: `src/scorer.py` (add hours_to_resolution parameter)
- Modify: `src/monitor.py` (compute hours_to_resolution, pass to scorer)

- [ ] **Step 1: Add time-proximity factor to scorer**

Add parameter to `score_trade()` signature:

```python
    hours_to_resolution: float | None = None,
```

Add new scoring section after repeat offender (new section 8):

```python
    # --- 8. Time proximity to resolution (max 15 pts) ---
    # Every insider case: bets placed <72h before event.
    # Maduro: <1h. Iran/Magamyman: 71min. Nobel: ~11h. OpenAI: <40h.
    if hours_to_resolution is not None:
        if hours_to_resolution <= 6:
            breakdown.add("time_proximity", 15)
        elif hours_to_resolution <= 24:
            breakdown.add("time_proximity", 12)
        elif hours_to_resolution <= 72:
            breakdown.add("time_proximity", 8)
        elif hours_to_resolution <= 168:  # 1 week
            breakdown.add("time_proximity", 3)
```

- [ ] **Step 2: Compute hours_to_resolution in monitor**

In `src/monitor.py`, after fetching market_info (around line 138), add:

```python
            # Compute hours to market resolution
            hours_to_resolution = None
            end_date_str = market_info.get("end_date", "")
            if end_date_str:
                try:
                    from datetime import datetime, timezone
                    end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                    hours_to_resolution = max(
                        0, (end_date - datetime.now(timezone.utc)).total_seconds() / 3600
                    )
                except (ValueError, TypeError):
                    pass
```

Update the `score_trade()` call to pass `hours_to_resolution=hours_to_resolution`.

- [ ] **Step 3: Commit**

```bash
git add src/scorer.py src/monitor.py
git commit -m "feat: add time-to-resolution proximity scoring"
```

### Task 2.3: Store hours_to_resolution in signals table

**Files:**
- Modify: `src/database.py` (add column migration, update save_signal)
- Modify: `src/monitor.py` (pass hours_to_resolution to save_signal)

- [ ] **Step 1: Add column migration in init_db()**

Add after existing migrations in `init_db()`:

```python
# Add hours_to_resolution column
try:
    await conn.execute("""
        ALTER TABLE signals ADD COLUMN IF NOT EXISTS
            hours_to_resolution DOUBLE PRECISION
    """)
except Exception:
    pass
```

- [ ] **Step 2: Update save_signal to include hours_to_resolution**

In `save_signal()` in `src/database.py`, add `hours_to_resolution` to:
- The column list in the INSERT statement (after `unique_markets`)
- The VALUES placeholders (add `%(hours_to_resolution)s`)
- The function accepts a `dict`, so no signature change needed — just add the key to the INSERT.

- [ ] **Step 3: Pass from monitor to save_signal**

Add `"hours_to_resolution": hours_to_resolution` to the signal dict in monitor.py.

- [ ] **Step 4: Commit**

```bash
git add src/database.py src/monitor.py
git commit -m "feat: persist hours_to_resolution in signals table"
```

---

## Phase 3: Funding Source Analysis

Trace where wallet USDC came from. Fresh wallets funded from the same exchange address or with round-number deposits are a strong correlation signal. The Iran case (6 wallets, shared Binance address) and OpenAI case (13 wallets, similar funding patterns) both had this.

### Task 3.1: Create wallet funding tracker

**Files:**
- Create: `src/funding.py`
- Modify: `src/database.py` (add wallet_funding table)

- [ ] **Step 1: Add wallet_funding table**

Add to `init_db()`:

```python
await conn.execute("""
    CREATE TABLE IF NOT EXISTS wallet_funding (
        wallet TEXT PRIMARY KEY,
        funding_source TEXT,
        funding_tx_hash TEXT,
        funding_amount_usdc DOUBLE PRECISION,
        funding_timestamp TIMESTAMPTZ,
        is_round_amount BOOLEAN DEFAULT FALSE,
        source_type TEXT DEFAULT 'unknown',
        checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_funding_source
        ON wallet_funding(funding_source);
""")
```

- [ ] **Step 2: Create funding.py module**

```python
"""Trace wallet funding sources on Polygon.

Looks at the first USDC transfer into a wallet to determine where
the funds came from — exchange hot wallet, another user wallet, etc.
"""

import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

# Known exchange hot wallets on Polygon (USDC transfers)
KNOWN_EXCHANGES = {
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "Binance",
    "0xe7804c37c13166ff0b37f5ae0bb07a3aebb6e245": "Binance",
    "0x6cc68aCF01A754Ef9a82B1EE0822b88e52559431": "Coinbase",
    "0x0d0707963952f2fba59dd06f2b425ace40b492fe": "Gate.io",
    "0x28c6c06298d514db089934071355e5743bf21d60": "Binance",
    "0x1AB4973a48dc892Cd9971ECE8e01DcC7688f8F23": "Bybit",
    "0xf89d7b9c864f589bbF53a82105107622B35EaA40": "Bybit",
}

USDC_POLYGON = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
USDC_BRIDGED = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

POLYGONSCAN_API = "https://api.polygonscan.com/api"


async def get_funding_source(
    wallet: str, client: httpx.AsyncClient
) -> dict:
    """Find the first USDC deposit into this wallet.

    Returns dict with: funding_source, funding_amount_usdc,
    funding_tx_hash, funding_timestamp, is_round_amount, source_type
    """
    result = {
        "funding_source": None,
        "funding_amount_usdc": None,
        "funding_tx_hash": None,
        "funding_timestamp": None,
        "is_round_amount": False,
        "source_type": "unknown",
    }

    try:
        # Query ERC-20 token transfers TO this wallet (USDC)
        # Using Polygonscan API (free tier: 5 calls/sec)
        for usdc_addr in [USDC_POLYGON, USDC_BRIDGED]:
            resp = await client.get(
                POLYGONSCAN_API,
                params={
                    "module": "account",
                    "action": "tokentx",
                    "contractaddress": usdc_addr,
                    "address": wallet,
                    "page": "1",
                    "offset": "10",
                    "sort": "asc",
                },
                timeout=10,
            )
            if resp.status_code != 200:
                continue

            data = resp.json()
            if data.get("status") != "1" or not data.get("result"):
                continue

            # Find first incoming transfer
            for tx in data["result"]:
                if tx["to"].lower() == wallet.lower():
                    source = tx["from"].lower()
                    decimals = int(tx.get("tokenDecimal", 6))
                    amount = int(tx["value"]) / (10 ** decimals)

                    result["funding_source"] = source
                    result["funding_amount_usdc"] = amount
                    result["funding_tx_hash"] = tx["hash"]
                    result["funding_timestamp"] = datetime.fromtimestamp(
                        int(tx["timeStamp"]), tz=timezone.utc
                    ).isoformat()

                    # Check if round amount (exact thousands)
                    result["is_round_amount"] = (
                        amount >= 100 and amount % 1000 == 0
                    )

                    # Check against known exchanges
                    if source in KNOWN_EXCHANGES:
                        result["source_type"] = f"exchange:{KNOWN_EXCHANGES[source]}"
                    else:
                        result["source_type"] = "wallet"

                    return result

    except Exception as e:
        logger.warning(f"Failed to trace funding for {wallet[:10]}...: {e}")

    return result
```

- [ ] **Step 3: Commit**

```bash
git add src/funding.py src/database.py
git commit -m "feat: add wallet funding source tracker"
```

### Task 3.2: Integrate funding analysis into monitor

**Files:**
- Modify: `src/monitor.py` (call get_funding_source, save results)
- Modify: `src/database.py` (add save function)
- Modify: `src/scorer.py` (add funding-based scoring)

- [ ] **Step 1: Add save_wallet_funding to database.py**

```python
async def save_wallet_funding(wallet: str, funding: dict) -> None:
    """Save wallet funding source info."""
    async with _pool.connection() as conn:
        await conn.execute("""
            INSERT INTO wallet_funding (
                wallet, funding_source, funding_tx_hash,
                funding_amount_usdc, funding_timestamp,
                is_round_amount, source_type
            ) VALUES (
                %(wallet)s, %(funding_source)s, %(funding_tx_hash)s,
                %(funding_amount_usdc)s, %(funding_timestamp)s,
                %(is_round_amount)s, %(source_type)s
            )
            ON CONFLICT (wallet) DO NOTHING
        """, {"wallet": wallet.lower(), **funding})
```

- [ ] **Step 2: Add shared-funding-source query**

```python
async def count_wallets_from_same_source(funding_source: str) -> int:
    """Count how many tracked wallets were funded by the same address."""
    if not funding_source:
        return 0
    async with _pool.connection() as conn:
        row = await conn.execute("""
            SELECT COUNT(*) FROM wallet_funding
            WHERE funding_source = %(source)s
        """, {"source": funding_source.lower()})
        return (await row.fetchone())[0]
```

- [ ] **Step 3: Call funding analysis in monitor**

In `src/monitor.py`, after the wallet reputation upsert, add:

```python
            # Trace funding source (async, non-blocking on failure)
            from .funding import get_funding_source
            from .database import save_wallet_funding, count_wallets_from_same_source
            funding = await get_funding_source(wallet, http_client)
            if funding["funding_source"]:
                await save_wallet_funding(wallet, funding)
                shared_source_count = await count_wallets_from_same_source(
                    funding["funding_source"]
                )
            else:
                shared_source_count = 0
```

- [ ] **Step 4: Add funding scoring to scorer**

Add parameters to `score_trade()`:

```python
    is_round_funding: bool = False,
    shared_funding_source_count: int = 0,
```

Add scoring section:

```python
    # --- 9. Funding source suspicion (max 10 pts) ---
    # Iran case: 6 wallets from shared Binance address.
    # Round-number deposits + shared source = coordinated.
    if shared_funding_source_count >= 3:
        breakdown.add("shared_funding", 7)
    elif shared_funding_source_count >= 2:
        breakdown.add("shared_funding", 4)

    if is_round_funding:
        breakdown.add("round_funding", 3)
```

- [ ] **Step 5: Pass funding data to scorer from monitor**

Update the `score_trade()` call:

```python
                is_round_funding=funding.get("is_round_amount", False),
                shared_funding_source_count=shared_source_count,
```

- [ ] **Step 6: Commit**

```bash
git add src/monitor.py src/database.py src/scorer.py
git commit -m "feat: integrate funding source analysis into scoring"
```

### Task 3.3: Add Polygonscan API key to config

**Files:**
- Modify: `src/config.py`

- [ ] **Step 1: Add optional POLYGONSCAN_API_KEY**

The free tier works without a key (5 calls/sec). Add to config for higher rate limits:

```python
    polygonscan_api_key: str = os.getenv("POLYGONSCAN_API_KEY", "")
```

- [ ] **Step 2: Update funding.py to use API key if available**

In `funding.py`, import config and add to Polygonscan request params:

```python
from .config import config

# In get_funding_source(), add to params dict:
params = {
    "module": "account",
    "action": "tokentx",
    # ... existing params ...
}
if config.polygonscan_api_key:
    params["apikey"] = config.polygonscan_api_key
```

- [ ] **Step 3: Commit**

```bash
git add src/config.py src/funding.py
git commit -m "feat: support optional Polygonscan API key for higher rate limits"
```

---

## Phase 4: Wave Detection

Detect coordinated buying across multiple wallets in a short time window. Go beyond simple cluster counting — detect synchronized timing, shared funding, and price impact.

### Task 4.1: Create wave detection module

**Files:**
- Create: `src/waves.py`
- Modify: `src/database.py` (add wave-related queries)

- [ ] **Step 1: Add wave_events table**

Add to `init_db()`:

```python
await conn.execute("""
    CREATE TABLE IF NOT EXISTS wave_events (
        id SERIAL PRIMARY KEY,
        condition_id TEXT NOT NULL,
        outcome TEXT NOT NULL,
        detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        wallet_count INT NOT NULL,
        total_volume_usdc DOUBLE PRECISION NOT NULL,
        time_window_minutes INT NOT NULL,
        wallets TEXT[] NOT NULL,
        avg_suspicion_score DOUBLE PRECISION,
        shared_funding_source TEXT,
        price_before DOUBLE PRECISION,
        price_after DOUBLE PRECISION
    );
    CREATE INDEX IF NOT EXISTS idx_wave_condition
        ON wave_events(condition_id);
""")
```

- [ ] **Step 2: Create waves.py**

```python
"""Detect coordinated buying waves across multiple wallets.

A "wave" is when multiple new/suspicious wallets pile into the same
outcome within a short time window. This is the #1 pattern in
multi-wallet insider schemes (Iran: 6 wallets, OpenAI: 13, Axiom: 12).
"""

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


async def detect_wave(
    condition_id: str,
    outcome: str,
    current_wallet: str,
) -> dict | None:
    """Check if the current signal is part of a wave.

    Looks for 3+ distinct wallets betting on the same outcome
    within the last 2 hours. Returns wave info or None.
    """
    from .database import _pool
    async with _pool.connection() as conn:
        window = datetime.now(timezone.utc) - timedelta(hours=2)

        rows = await conn.execute("""
            SELECT
                COUNT(DISTINCT wallet) as wallet_count,
                SUM(trade_size_usdc) as total_volume,
                array_agg(DISTINCT wallet) as wallets,
                AVG(suspicion_score) as avg_score
            FROM signals
            WHERE condition_id = %(cid)s
              AND outcome = %(outcome)s
              AND created_at >= %(window)s
        """, {
            "cid": condition_id,
            "outcome": outcome,
            "window": window,
        })
        row = await rows.fetchone()

        if row and row[0] >= 3:
            # Check if wallets share a funding source
            shared_source = None
            wallet_list = row[2]
            if wallet_list:
                source_rows = await conn.execute("""
                    SELECT funding_source, COUNT(*) as cnt
                    FROM wallet_funding
                    WHERE wallet = ANY(%(wallets)s)
                      AND funding_source IS NOT NULL
                    GROUP BY funding_source
                    HAVING COUNT(*) >= 2
                    ORDER BY cnt DESC
                    LIMIT 1
                """, {"wallets": [w.lower() for w in wallet_list]})
                source_row = await source_rows.fetchone()
                if source_row:
                    shared_source = source_row[0]

            return {
                "wallet_count": row[0],
                "total_volume_usdc": float(row[1] or 0),
                "wallets": wallet_list,
                "avg_suspicion_score": float(row[3]) if row[3] else None,
                "shared_funding_source": shared_source,
                "time_window_minutes": 120,
            }

    return None
```

- [ ] **Step 3: Commit**

```bash
git add src/waves.py src/database.py
git commit -m "feat: add wave detection module"
```

### Task 4.2: Integrate wave detection into monitor and alerts

**Files:**
- Modify: `src/monitor.py` (call detect_wave, boost score)
- Modify: `src/scorer.py` (enhance cluster scoring with wave data)
- Modify: `src/notifier.py` (add wave warning to Telegram alerts)
- Modify: `src/database.py` (add save_wave_event function)

- [ ] **Step 1: Add save_wave_event to database.py**

```python
async def save_wave_event(
    condition_id: str, outcome: str, wave: dict
) -> None:
    """Persist a detected wave event."""
    async with _pool.connection() as conn:
        await conn.execute("""
            INSERT INTO wave_events (
                condition_id, outcome, wallet_count,
                total_volume_usdc, time_window_minutes,
                wallets, avg_suspicion_score, shared_funding_source
            ) VALUES (
                %(cid)s, %(outcome)s, %(wallet_count)s,
                %(total_volume)s, %(window)s,
                %(wallets)s, %(avg_score)s, %(shared_source)s
            )
        """, {
            "cid": condition_id,
            "outcome": outcome,
            "wallet_count": wave["wallet_count"],
            "total_volume": wave["total_volume_usdc"],
            "window": wave["time_window_minutes"],
            "wallets": wave["wallets"],
            "avg_score": wave.get("avg_suspicion_score"),
            "shared_source": wave.get("shared_funding_source"),
        })
```

- [ ] **Step 2: Call wave detection in monitor**

In `src/monitor.py`, after saving the signal, add:

```python
            # Detect coordinated waves
            from .waves import detect_wave
            from .database import save_wave_event
            wave = await detect_wave(
                condition_id=market_info.get("condition_id", ""),
                outcome=market_info.get("outcome", ""),
                current_wallet=wallet,
            )
            if wave:
                await save_wave_event(
                    condition_id=market_info.get("condition_id", ""),
                    outcome=market_info.get("outcome", ""),
                    wave=wave,
                )
                logger.warning(
                    f"🌊 WAVE DETECTED: {wave['wallet_count']} wallets | "
                    f"${wave['total_volume_usdc']:,.0f} total | "
                    f"{market_info.get('title', '?')[:40]}"
                )
```

- [ ] **Step 3: Add wave indicator to Telegram alerts**

In `src/notifier.py`, add optional `wave` parameter to `send_whale_alert()`:

```python
    wave: dict | None = None,
```

Add wave section to message (after score_section):

```python
    wave_section = ""
    if wave and wave.get("wallet_count", 0) >= 3:
        wave_section = (
            f"\n🌊 <b>WAVE ALERT: {wave['wallet_count']} wallets</b>\n"
            f"Combined volume: ${wave['total_volume_usdc']:,.0f}\n"
        )
        if wave.get("shared_funding_source"):
            wave_section += f"⚠️ Shared funding source detected\n"
```

Include `{wave_section}` in the message template.

- [ ] **Step 4: Pass wave to notifier from monitor**

Update the `send_whale_alert()` call in monitor to include `wave=wave`.

- [ ] **Step 5: Commit**

```bash
git add src/monitor.py src/scorer.py src/notifier.py src/database.py src/waves.py
git commit -m "feat: integrate wave detection with alerts and persistence"
```

---

## Phase 5: Dashboard Enhancements

Surface all the new intelligence in the dashboard.

### Task 5.1: Add waves view to dashboard

**Files:**
- Modify: `dashboard/src/app/page.tsx` (add wave alerts section)

- [ ] **Step 1: Query recent waves**

Add a new query in `getStats()`:

```typescript
const waves = await sql`
    SELECT w.*, s.market_title, s.outcome
    FROM wave_events w
    LEFT JOIN LATERAL (
        SELECT market_title, outcome FROM signals
        WHERE condition_id = w.condition_id LIMIT 1
    ) s ON true
    ORDER BY w.detected_at DESC
    LIMIT 10
` as unknown as any[];
```

Return `waves` from the stats object.

- [ ] **Step 2: Render wave alerts above the signals table**

```tsx
{stats.waves && stats.waves.length > 0 && (
  <div className="rounded-lg border border-orange-800/50 bg-orange-950/20">
    <div className="px-4 py-3 border-b border-orange-800/50">
      <h2 className="text-sm font-medium text-orange-300">
        🌊 Recent Wave Activity
      </h2>
    </div>
    <div className="divide-y divide-orange-800/30">
      {stats.waves.map((w: any) => (
        <div key={w.id} className="px-4 py-2 text-sm">
          <span className="text-orange-300 font-mono">
            {w.wallet_count} wallets
          </span>
          <span className="text-zinc-400 mx-2">·</span>
          <span className="font-mono">
            ${Number(w.total_volume_usdc).toLocaleString()}
          </span>
          <span className="text-zinc-400 mx-2">·</span>
          <span className="text-zinc-300 truncate">
            {w.market_title}
          </span>
          {w.shared_funding_source && (
            <span className="ml-2 text-xs px-1 py-0.5 rounded bg-red-900/50 text-red-300">
              shared funding
            </span>
          )}
        </div>
      ))}
    </div>
  </div>
)}
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/app/page.tsx
git commit -m "feat: add wave activity section to dashboard"
```

### Task 5.2: Add time-to-resolution display

**Files:**
- Modify: `dashboard/src/app/page.tsx`
- Modify: `dashboard/src/lib/types.ts`

- [ ] **Step 1: Add hours_to_resolution to Signal type**

```typescript
  hours_to_resolution?: number;
```

- [ ] **Step 2: Include in query and display**

Add `s.hours_to_resolution` to the recent signals SELECT.

Add a column or inline indicator showing urgency:

```tsx
{s.hours_to_resolution != null && s.hours_to_resolution <= 72 && (
  <span className={`ml-1 text-xs px-1 py-0.5 rounded ${
    s.hours_to_resolution <= 6
      ? "bg-red-900/50 text-red-300"
      : s.hours_to_resolution <= 24
        ? "bg-orange-900/50 text-orange-300"
        : "bg-yellow-900/50 text-yellow-300"
  }`}>
    {s.hours_to_resolution <= 1
      ? "<1h"
      : s.hours_to_resolution <= 24
        ? `${Math.round(s.hours_to_resolution)}h`
        : `${Math.round(s.hours_to_resolution / 24)}d`
    }
  </span>
)}
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/app/page.tsx dashboard/src/lib/types.ts
git commit -m "feat: show time-to-resolution urgency badges in dashboard"
```

### Task 5.3: Update scoring explanation page

**Files:**
- Modify: `dashboard/src/app/scoring/page.tsx`

- [ ] **Step 1: Add new scoring factors to the explanation page**

Update the scoring explanation to document all new factors:
- Repeat winner (max 15 pts)
- Repeat offender (max 5 pts)
- Time proximity (max 15 pts)
- Shared funding (max 7 pts)
- Round funding (max 3 pts)

Update the max possible score from 100 to reflect new factors (still capped at 100).

- [ ] **Step 2: Add real-world case studies section**

Add a section showing which factors would have triggered on each documented case:

```
Venezuela/Maduro: age(25) + low_prob(25) + size(15) + concentration(15) + time(15) = 95
Iran/IDF: age(25) + low_prob(25) + size(15) + repeat(15) + time(15) = 95
Iran/6 wallets: age(25) + low_prob(25) + size(12) + cluster(10) + shared_funding(7) + time(15) = 94
AlphaRaccoon: low_prob(25) + size(15) + concentration(15) + repeat(15) + round_funding(3) = 73
OpenAI/13 wallets: age(25) + low_prob(20) + cluster(10) + shared_funding(7) + time(12) = 74
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/app/scoring/page.tsx
git commit -m "feat: update scoring docs with new factors and case studies"
```

---

## Phase 6: Final Integration & Testing

### Task 6.1: End-to-end integration test

**Files:**
- Create: `tests/test_scoring_enhanced.py`
- Create: `tests/__init__.py` (empty)

- [ ] **Step 0: Create tests directory**

Run: `mkdir -p tests && touch tests/__init__.py`

- [ ] **Step 1: Write scoring tests for all new factors**

```python
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
    assert score.total >= 80, f"AlphaRaccoon pattern should score 80+, got {score.total}"
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
    assert score.total >= 95, f"Iran wave pattern should score 95+, got {score.total}"


def test_normal_whale_scores_low():
    """Established trader, high-prob bet, no red flags."""
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
    assert score.total <= 20, f"Normal whale should score ≤20, got {score.total}"
    assert score.tier == "LOW"
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_scoring_enhanced.py -v`
Expected: All 4 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_scoring_enhanced.py
git commit -m "test: add enhanced scoring tests for insider patterns"
```

### Task 6.2: Update notifier scoring factor labels

**Files:**
- Modify: `src/notifier.py`

- [ ] **Step 1: Add new factor labels to the Telegram message builder**

Update the `factor_labels` dict in `send_whale_alert()` (around line 116):

```python
        factor_labels = {
            "age": "Account Age",
            "low_prob": "Low Probability",
            "size": "Trade Size",
            "concentration": "Concentrated",
            "size_ratio": "Size vs History",
            "cluster": "Cluster Activity",
            "repeat_winner": "🔥 Repeat Winner",
            "repeat_offender": "Repeat Offender",
            "time_proximity": "⏰ Imminent Resolution",
            "shared_funding": "Shared Funding Source",
            "round_funding": "Round Deposit Amount",
        }
```

- [ ] **Step 2: Commit**

```bash
git add src/notifier.py
git commit -m "feat: add new scoring factor labels to Telegram alerts"
```

### Task 6.3: Final commit and push

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/ -v
```

- [ ] **Step 2: Push all changes**

```bash
git push
```
