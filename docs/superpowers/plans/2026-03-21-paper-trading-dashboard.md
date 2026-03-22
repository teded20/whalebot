# Paper Trading System + Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent signal tracking, automated settlement checking, and a shared web dashboard so two users can evaluate whether tailing new-whale bets on Polymarket is profitable before risking real money.

**Architecture:** The existing Python bot gets a Postgres persistence layer (Neon free tier) that logs every whale signal with entry price. A settlement checker runs periodically to resolve outcomes. A Next.js app on Vercel free tier reads from the same DB to display a signal feed, win rates by threshold, and cumulative P&L charts.

**Tech Stack:** Python 3.12 (bot) + psycopg 3 (async Postgres) | Neon Postgres free tier (shared DB) | Next.js 16 + Tailwind (dashboard) | Vercel free tier (hosting)

---

## File Structure

### Python Bot (existing `src/` — modifications + new files)

| File | Action | Responsibility |
|------|--------|---------------|
| `src/config.py` | Modify | Add `DATABASE_URL`, lower `MIN_TRADE_USDC` default to 500 |
| `src/database.py` | Create | Postgres connection pool, schema init, signal CRUD |
| `src/settlement.py` | Create | Periodic checker: query unresolved signals, check gamma API, update outcomes |
| `src/stats.py` | Create | Query DB for win rates by threshold/age/size, cumulative P&L |
| `src/monitor.py` | Modify | After sending alert, also save signal to DB with entry price |
| `src/market_resolver.py` | Modify | Add `get_current_price()` to fetch best ask from CLOB API |
| `src/main.py` | Modify | Init DB pool at startup, run settlement checker on interval |
| `settle.py` | Create (root) | Standalone script to run settlement check once (for cron/manual use) |
| `stats_cli.py` | Create (root) | CLI to print stats from DB (quick local check) |
| `requirements.txt` | Modify | Add `psycopg[binary]>=3.1`, `psycopg_pool>=3.1`, remove `aiosqlite` |
| `.env` | Modify | Add `DATABASE_URL` |

### Next.js Dashboard (new `dashboard/` directory)

| File | Action | Responsibility |
|------|--------|---------------|
| `dashboard/package.json` | Create | Next.js + Tailwind + @neondatabase/serverless deps |
| `dashboard/next.config.ts` | Create | Minimal config |
| `dashboard/tailwind.config.ts` | Create | Tailwind setup with dark mode |
| `dashboard/.env.local` | Create | `DATABASE_URL` for Neon |
| `dashboard/src/app/layout.tsx` | Create | Root layout, dark mode, Geist font |
| `dashboard/src/app/page.tsx` | Create | Main dashboard: stats summary + signal feed |
| `dashboard/src/app/signals/page.tsx` | Create | Full signal table with filters |
| `dashboard/src/lib/db.ts` | Create | Neon serverless client, reusable query helper |
| `dashboard/src/lib/types.ts` | Create | Signal, Stats TypeScript types |
| `dashboard/src/components/stats-cards.tsx` | Create | Win rate cards by threshold bucket |
| `dashboard/src/components/signal-table.tsx` | Create | Sortable/filterable signal table |
| `dashboard/src/components/pnl-chart.tsx` | Create | Cumulative P&L line chart |
| `dashboard/src/components/filters.tsx` | Create | Threshold, date range, account age filters |

---

## Prerequisite: Neon Database Setup (do this first)

Before starting implementation:

1. Go to https://neon.tech and create a free account
2. Create a new project (any region, free tier)
3. Copy the connection string from the dashboard
4. Paste it into `.env` as `DATABASE_URL`

The schema is created automatically by `init_db()` on first run.

---

## Part 1: Python Bot — Persistence Layer

### Task 1: Config + Dependencies

**Files:**
- Modify: `src/config.py`
- Modify: `requirements.txt`
- Modify: `.env`

- [ ] **Step 1: Add psycopg to requirements.txt**

```
# Replace aiosqlite with:
psycopg[binary]>=3.1
psycopg_pool>=3.1
# Remove: aiosqlite>=0.19.0
```

- [ ] **Step 2: Add DATABASE_URL to config**

In `src/config.py`, add to the `Config` dataclass:

```python
# Database
database_url: str = os.getenv("DATABASE_URL", "")
```

And in `validate()`, add:

```python
if not self.database_url:
    errors.append("DATABASE_URL is required")
```

- [ ] **Step 3: Lower default MIN_TRADE_USDC**

In `src/config.py`, change the default:

```python
min_trade_usdc: float = float(os.getenv("MIN_TRADE_USDC", "500"))
```

- [ ] **Step 4: Add DATABASE_URL to .env**

```
DATABASE_URL=postgresql://user:pass@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require
```

(User will fill in actual Neon connection string after creating the DB)

- [ ] **Step 5: Install deps**

Run: `pip install -r requirements.txt`
Expected: psycopg installs successfully

- [ ] **Step 6: Commit**

```bash
git add src/config.py requirements.txt
git commit -m "feat: add DATABASE_URL config, lower min trade to $500"
```

---

### Task 2: Database Module

**Files:**
- Create: `src/database.py`
- Test: `tests/test_database.py`

- [ ] **Step 1: Write the failing test**

Create `tests/__init__.py` (empty) and `tests/test_database.py`:

```python
"""Tests for database module.

These tests use a real Neon database (from DATABASE_URL env var).
Skip if not configured.
"""
import asyncio
import os
import time
import pytest
from unittest.mock import AsyncMock

# Skip all tests if no DATABASE_URL
pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set"
)

from src.database import init_db, save_signal, get_unresolved_signals, update_signal_resolution, get_stats


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def db(event_loop):
    """Initialize DB once for all tests."""
    event_loop.run_until_complete(init_db())
    yield
    # Clean up test data
    from src.database import _pool
    async def cleanup():
        async with _pool.connection() as conn:
            await conn.execute("DELETE FROM signals WHERE wallet = '0xTEST_WALLET'")
    event_loop.run_until_complete(cleanup())


def test_save_and_retrieve_signal(db, event_loop):
    """Save a signal, retrieve it as unresolved."""
    signal_data = {
        "wallet": "0xTEST_WALLET",
        "trade_size_usdc": 5000.0,
        "side": "BUY",
        "ctf_token_id": "12345",
        "market_title": "Test Market",
        "outcome": "Yes",
        "exchange": "CTF Exchange",
        "tx_hash": "0xabc123",
        "account_age_days": 2.0,
        "total_trades": 3,
        "total_volume_usdc": 8000.0,
        "entry_price": 0.65,
        "pseudonym": "TestWhale",
        "condition_id": "0xcond123",
        "market_slug": "test-market",
    }

    signal_id = event_loop.run_until_complete(save_signal(signal_data))
    assert signal_id is not None
    assert signal_id > 0

    # Should appear in unresolved signals
    unresolved = event_loop.run_until_complete(get_unresolved_signals())
    wallet_signals = [s for s in unresolved if s["wallet"] == "0xTEST_WALLET"]
    assert len(wallet_signals) >= 1
    assert wallet_signals[0]["market_title"] == "Test Market"
    assert wallet_signals[0]["entry_price"] == 0.65


def test_update_resolution(db, event_loop):
    """Resolve a signal and verify it no longer appears as unresolved."""
    # Save a new signal
    signal_data = {
        "wallet": "0xTEST_WALLET",
        "trade_size_usdc": 10000.0,
        "side": "BUY",
        "ctf_token_id": "67890",
        "market_title": "Resolved Market",
        "outcome": "Yes",
        "exchange": "NegRisk",
        "tx_hash": "0xdef456",
        "account_age_days": 1.0,
        "total_trades": 1,
        "total_volume_usdc": 10000.0,
        "entry_price": 0.40,
        "pseudonym": None,
        "condition_id": "0xcond456",
        "market_slug": "resolved-market",
    }
    signal_id = event_loop.run_until_complete(save_signal(signal_data))

    # Resolve it as a win
    event_loop.run_until_complete(
        update_signal_resolution(signal_id, won=True, winning_outcome="Yes")
    )

    # Should NOT appear in unresolved
    unresolved = event_loop.run_until_complete(get_unresolved_signals())
    ids = [s["id"] for s in unresolved]
    assert signal_id not in ids


def test_get_stats(db, event_loop):
    """Stats should include our test signals."""
    stats = event_loop.run_until_complete(get_stats())
    assert stats["total_signals"] >= 1
    # Should have threshold buckets
    assert len(stats["by_threshold"]) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_database.py -v`
Expected: ImportError — `src.database` doesn't exist yet

- [ ] **Step 3: Write the database module**

Create `src/database.py`:

```python
"""Persistent signal storage using Postgres (Neon).

Stores every whale signal with entry price, then tracks
resolution status for paper-trading P&L calculation.
"""

import logging
import time
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .config import config

logger = logging.getLogger(__name__)

_pool: Optional[AsyncConnectionPool] = None

THRESHOLD_BUCKETS = [500, 1_000, 2_500, 5_000, 10_000, 25_000, 50_000, 100_000]

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id              SERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    wallet          TEXT NOT NULL,
    trade_size_usdc DOUBLE PRECISION NOT NULL,
    side            TEXT NOT NULL,
    ctf_token_id    TEXT NOT NULL,
    market_title    TEXT NOT NULL DEFAULT '',
    outcome         TEXT NOT NULL DEFAULT '',
    exchange        TEXT NOT NULL DEFAULT '',
    tx_hash         TEXT NOT NULL DEFAULT '',
    account_age_days DOUBLE PRECISION NOT NULL DEFAULT 0,
    total_trades    INTEGER NOT NULL DEFAULT 0,
    total_volume_usdc DOUBLE PRECISION NOT NULL DEFAULT 0,
    entry_price     DOUBLE PRECISION,
    pseudonym       TEXT,
    condition_id    TEXT NOT NULL DEFAULT '',
    market_slug     TEXT NOT NULL DEFAULT '',
    -- Resolution fields (filled in later by settlement checker)
    resolved        BOOLEAN NOT NULL DEFAULT FALSE,
    won             BOOLEAN,
    winning_outcome TEXT,
    resolved_at     TIMESTAMPTZ,
    -- Indexes
    UNIQUE(tx_hash, wallet, ctf_token_id)
);

CREATE INDEX IF NOT EXISTS idx_signals_resolved ON signals(resolved) WHERE NOT resolved;
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
CREATE INDEX IF NOT EXISTS idx_signals_size ON signals(trade_size_usdc);
"""


async def init_db():
    """Initialize connection pool and create schema."""
    global _pool
    if _pool is not None:
        return

    _pool = AsyncConnectionPool(
        config.database_url,
        min_size=1,
        max_size=5,
        open=False,
        kwargs={"row_factory": dict_row},
    )
    await _pool.open()

    async with _pool.connection() as conn:
        for statement in SCHEMA.split(";"):
            statement = statement.strip()
            if statement:
                await conn.execute(statement)
        await conn.commit()

    logger.info("Database initialized")


async def close_db():
    """Close the connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def save_signal(data: dict) -> Optional[int]:
    """Save a whale signal to the database.

    Returns the signal ID, or None if it was a duplicate.
    """
    if _pool is None:
        logger.warning("Database not initialized — signal not saved")
        return None

    try:
        async with _pool.connection() as conn:
            row = await conn.execute(
                """
                INSERT INTO signals (
                    wallet, trade_size_usdc, side, ctf_token_id,
                    market_title, outcome, exchange, tx_hash,
                    account_age_days, total_trades, total_volume_usdc,
                    entry_price, pseudonym, condition_id, market_slug
                ) VALUES (
                    %(wallet)s, %(trade_size_usdc)s, %(side)s, %(ctf_token_id)s,
                    %(market_title)s, %(outcome)s, %(exchange)s, %(tx_hash)s,
                    %(account_age_days)s, %(total_trades)s, %(total_volume_usdc)s,
                    %(entry_price)s, %(pseudonym)s, %(condition_id)s, %(market_slug)s
                )
                ON CONFLICT (tx_hash, wallet, ctf_token_id) DO NOTHING
                RETURNING id
                """,
                data,
            )
            result = await row.fetchone()
            await conn.commit()

            if result:
                logger.info(f"Signal saved: id={result['id']} ${data['trade_size_usdc']:,.0f} on {data['market_title'][:40]}")
                return result["id"]
            else:
                logger.debug(f"Duplicate signal skipped: {data['tx_hash'][:10]}")
                return None

    except Exception as e:
        logger.error(f"Failed to save signal: {e}")
        return None


async def get_unresolved_signals(max_age_days: int = 90) -> list[dict]:
    """Get all unresolved signals younger than max_age_days."""
    if _pool is None:
        return []

    async with _pool.connection() as conn:
        cursor = await conn.execute(
            """
            SELECT * FROM signals
            WHERE NOT resolved
              AND created_at > NOW() - INTERVAL '1 day' * %s
            ORDER BY created_at ASC
            """,
            (max_age_days,),
        )
        return await cursor.fetchall()


async def update_signal_resolution(
    signal_id: int, won: bool, winning_outcome: str
):
    """Mark a signal as resolved with win/loss outcome."""
    if _pool is None:
        return

    async with _pool.connection() as conn:
        await conn.execute(
            """
            UPDATE signals
            SET resolved = TRUE, won = %s, winning_outcome = %s, resolved_at = NOW()
            WHERE id = %s
            """,
            (won, winning_outcome, signal_id),
        )
        await conn.commit()
        logger.info(f"Signal {signal_id} resolved: {'WIN' if won else 'LOSS'} (winner: {winning_outcome})")


async def get_stats() -> dict:
    """Compute paper-trading stats from the database.

    Returns:
        {
            "total_signals": int,
            "total_resolved": int,
            "total_wins": int,
            "total_losses": int,
            "total_pending": int,
            "by_threshold": [
                {"threshold": 500, "signals": N, "wins": N, "losses": N, "pending": N, "win_rate": float|None},
                ...
            ],
            "by_age": [
                {"bucket": "0-1d", "signals": N, "wins": N, "losses": N, "win_rate": float|None},
                ...
            ],
            "recent_signals": [...],  # last 20 signals
        }
    """
    if _pool is None:
        return {"total_signals": 0, "by_threshold": [], "by_age": [], "recent_signals": []}

    async with _pool.connection() as conn:
        # Overall totals
        row = await (await conn.execute(
            """
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE resolved AND won) as wins,
                COUNT(*) FILTER (WHERE resolved AND NOT won) as losses,
                COUNT(*) FILTER (WHERE NOT resolved) as pending
            FROM signals
            """
        )).fetchone()

        total_signals = row["total"]
        total_wins = row["wins"]
        total_losses = row["losses"]
        total_pending = row["pending"]

        # By threshold bucket
        by_threshold = []
        for threshold in THRESHOLD_BUCKETS:
            bucket = await (await conn.execute(
                """
                SELECT
                    COUNT(*) as signals,
                    COUNT(*) FILTER (WHERE resolved AND won) as wins,
                    COUNT(*) FILTER (WHERE resolved AND NOT won) as losses,
                    COUNT(*) FILTER (WHERE NOT resolved) as pending
                FROM signals
                WHERE trade_size_usdc >= %s
                """,
                (threshold,),
            )).fetchone()

            resolved = bucket["wins"] + bucket["losses"]
            by_threshold.append({
                "threshold": threshold,
                "signals": bucket["signals"],
                "wins": bucket["wins"],
                "losses": bucket["losses"],
                "pending": bucket["pending"],
                "win_rate": round(bucket["wins"] / resolved, 4) if resolved > 0 else None,
            })

        # By account age bucket
        age_buckets = [
            ("0-1d", 0, 1),
            ("1-3d", 1, 3),
            ("3-7d", 3, 7),
            ("7d+", 7, 9999),
        ]
        by_age = []
        for label, min_age, max_age in age_buckets:
            bucket = await (await conn.execute(
                """
                SELECT
                    COUNT(*) as signals,
                    COUNT(*) FILTER (WHERE resolved AND won) as wins,
                    COUNT(*) FILTER (WHERE resolved AND NOT won) as losses,
                    COUNT(*) FILTER (WHERE NOT resolved) as pending
                FROM signals
                WHERE account_age_days >= %s AND account_age_days < %s
                """,
                (min_age, max_age),
            )).fetchone()

            resolved = bucket["wins"] + bucket["losses"]
            by_age.append({
                "bucket": label,
                "signals": bucket["signals"],
                "wins": bucket["wins"],
                "losses": bucket["losses"],
                "pending": bucket["pending"],
                "win_rate": round(bucket["wins"] / resolved, 4) if resolved > 0 else None,
            })

        # Recent signals
        recent = await (await conn.execute(
            """
            SELECT id, created_at, wallet, trade_size_usdc, side,
                   market_title, outcome, account_age_days, total_trades,
                   entry_price, resolved, won, winning_outcome, market_slug
            FROM signals
            ORDER BY created_at DESC
            LIMIT 20
            """
        )).fetchall()

        return {
            "total_signals": total_signals,
            "total_resolved": total_wins + total_losses,
            "total_wins": total_wins,
            "total_losses": total_losses,
            "total_pending": total_pending,
            "by_threshold": by_threshold,
            "by_age": by_age,
            "recent_signals": recent,
        }
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_database.py -v`
Expected: All 3 tests pass (requires DATABASE_URL in .env)

- [ ] **Step 5: Commit**

```bash
git add src/database.py tests/
git commit -m "feat: add Postgres persistence layer for signal tracking"
```

---

### Task 3: Entry Price Capture

**Files:**
- Modify: `src/market_resolver.py`

- [ ] **Step 1: Add price fetching to market_resolver.py**

Add this function to `src/market_resolver.py`:

```python
CLOB_API = "https://clob.polymarket.com"


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
```

- [ ] **Step 2: Verify manually**

Run: `python -c "import asyncio, httpx; from src.market_resolver import get_current_price; ..."`
(Or test via a quick script that fetches a known token ID)

- [ ] **Step 3: Commit**

```bash
git add src/market_resolver.py
git commit -m "feat: add CLOB price fetching for entry price capture"
```

---

### Task 4: Wire Monitor to Save Signals

**Files:**
- Modify: `src/monitor.py`
- Modify: `src/main.py`

- [ ] **Step 1: Update monitor.py to save signals after alerting**

Add import at top of `src/monitor.py`:

```python
from .database import save_signal
from .market_resolver import get_current_price
```

In `process_order_filled()`, after the `await send_whale_alert(...)` call (line ~151), add:

```python
            # Capture current entry price (what we'd pay to tail)
            entry_price = await get_current_price(ctf_token_id, http_client)

            # Save signal to database for paper trading
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
            })
```

- [ ] **Step 2: Update main.py to init DB at startup**

Add import and init call in `src/main.py`:

```python
from .database import init_db, close_db
```

In the `start()` coroutine, add `await init_db()` before `await run_monitor()`.

- [ ] **Step 3: Test end-to-end with low threshold**

Run: `python -m src.main`
Wait for a signal to fire. Check the database has a row:
```bash
# Quick check via psql or:
python -c "
import asyncio
from src.database import init_db, get_stats
async def check():
    await init_db()
    stats = await get_stats()
    print(f'Signals in DB: {stats[\"total_signals\"]}')
asyncio.run(check())
"
```

- [ ] **Step 4: Commit**

```bash
git add src/monitor.py src/main.py
git commit -m "feat: save whale signals to Postgres with entry price"
```

---

### Task 5: Settlement Checker

**Files:**
- Create: `src/settlement.py`
- Create: `settle.py`

- [ ] **Step 1: Write the settlement module**

Create `src/settlement.py`:

```python
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
```

- [ ] **Step 2: Create standalone settle.py script**

Create `settle.py` at project root:

```python
#!/usr/bin/env python3
"""Run a one-off settlement check.

Usage: python settle.py
"""
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)

from src.database import init_db, close_db
from src.settlement import check_settlements


async def main():
    await init_db()
    try:
        checked, resolved, wins, losses = await check_settlements()
        print(f"\nDone: {checked} checked, {resolved} newly resolved ({wins}W / {losses}L)")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: Wire settlement into main.py as periodic task**

In `src/main.py`, add a background task that runs settlement every 6 hours:

```python
from .settlement import check_settlements

SETTLEMENT_INTERVAL = 6 * 60 * 60  # 6 hours


async def settlement_loop():
    """Run settlement checks periodically in the background."""
    logger = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(SETTLEMENT_INTERVAL)
        try:
            await check_settlements()
        except Exception as e:
            logger.error(f"Settlement check failed: {e}")
```

In the `start()` coroutine, launch it as a background task:

```python
asyncio.create_task(settlement_loop())
```

- [ ] **Step 4: Commit**

```bash
git add src/settlement.py settle.py src/main.py
git commit -m "feat: add settlement checker for paper trading resolution"
```

---

### Task 6: Stats CLI

**Files:**
- Create: `stats_cli.py`

- [ ] **Step 1: Create stats CLI script**

Create `stats_cli.py` at project root:

```python
#!/usr/bin/env python3
"""Print paper-trading stats from the database.

Usage: python stats_cli.py
"""
import asyncio
import logging

logging.basicConfig(level=logging.WARNING)

from src.database import init_db, close_db, get_stats


async def main():
    await init_db()
    try:
        stats = await get_stats()
    finally:
        await close_db()

    print()
    print("=" * 75)
    print("PAPER TRADING STATS")
    print("=" * 75)
    print()
    print(f"Total signals:  {stats['total_signals']}")
    print(f"Resolved:       {stats['total_resolved']}")
    print(f"Wins:           {stats['total_wins']}")
    print(f"Losses:         {stats['total_losses']}")
    print(f"Pending:        {stats['total_pending']}")

    overall_wr = "N/A"
    if stats["total_resolved"] > 0:
        overall_wr = f"{stats['total_wins'] / stats['total_resolved'] * 100:.1f}%"
    print(f"Win rate:       {overall_wr}")
    print()

    # By threshold
    print(f"{'Threshold':>12} | {'Signals':>8} | {'Wins':>6} | {'Losses':>6} | {'Pending':>8} | {'Win Rate':>8}")
    print("-" * 65)
    for b in stats["by_threshold"]:
        wr = f"{b['win_rate'] * 100:.0f}%" if b["win_rate"] is not None else "N/A"
        print(f"  ${b['threshold']:>9,} | {b['signals']:>8} | {b['wins']:>6} | {b['losses']:>6} | {b['pending']:>8} | {wr:>8}")
    print()

    # By account age
    print(f"{'Age Bucket':>12} | {'Signals':>8} | {'Wins':>6} | {'Losses':>6} | {'Pending':>8} | {'Win Rate':>8}")
    print("-" * 65)
    for b in stats["by_age"]:
        wr = f"{b['win_rate'] * 100:.0f}%" if b["win_rate"] is not None else "N/A"
        print(f"  {b['bucket']:>9} | {b['signals']:>8} | {b['wins']:>6} | {b['losses']:>6} | {b['pending']:>8} | {wr:>8}")
    print()

    # Recent signals
    if stats["recent_signals"]:
        print("RECENT SIGNALS (last 20):")
        print("-" * 75)
        for s in stats["recent_signals"]:
            status = "PENDING"
            if s.get("resolved"):
                status = "WIN" if s.get("won") else "LOSS"
            price_str = f"@{s['entry_price']:.2f}" if s.get("entry_price") else ""
            print(
                f"  ${s['trade_size_usdc']:>9,.0f} {s['side']:>4} {price_str:>6} | "
                f"{s['market_title'][:35]:<35} | "
                f"{s['outcome']:<8} | {status:<7} | "
                f"age={s['account_age_days']}d"
            )
        print()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Test it**

Run: `python stats_cli.py`
Expected: Prints stats table (may be empty if no signals yet)

- [ ] **Step 3: Commit**

```bash
git add stats_cli.py
git commit -m "feat: add CLI stats reporter for paper trading"
```

---

## Part 2: Next.js Dashboard

### Task 7: Scaffold Dashboard

**Files:**
- Create: `dashboard/` (entire Next.js project)

- [ ] **Step 1: Create Next.js app**

```bash
cd /Users/tyleredwards/Documents/GitHub/whalebot
npx create-next-app@latest dashboard --ts --tailwind --app --src-dir --no-eslint --import-alias "@/*"
```

- [ ] **Step 2: Install Neon serverless driver**

```bash
cd dashboard && npm install @neondatabase/serverless
```

- [ ] **Step 3: Create .env.local**

```
DATABASE_URL=postgresql://user:pass@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require
```

(Same connection string as the Python bot)

- [ ] **Step 4: Add dashboard/.env.local to root .gitignore**

Append to `.gitignore`:
```
dashboard/.env*.local
```

- [ ] **Step 5: Commit**

```bash
cd /Users/tyleredwards/Documents/GitHub/whalebot
git add dashboard/ .gitignore
git commit -m "feat: scaffold Next.js dashboard for paper trading stats"
```

---

### Task 8: Database Client + Types

**Files:**
- Create: `dashboard/src/lib/db.ts`
- Create: `dashboard/src/lib/types.ts`

- [ ] **Step 1: Create types**

Create `dashboard/src/lib/types.ts`:

```typescript
export interface Signal {
  id: number;
  created_at: string;
  wallet: string;
  trade_size_usdc: number;
  side: "BUY" | "SELL";
  ctf_token_id: string;
  market_title: string;
  outcome: string;
  exchange: string;
  tx_hash: string;
  account_age_days: number;
  total_trades: number;
  total_volume_usdc: number;
  entry_price: number | null;
  pseudonym: string | null;
  condition_id: string;
  market_slug: string;
  resolved: boolean;
  won: boolean | null;
  winning_outcome: string | null;
  resolved_at: string | null;
}

export interface ThresholdBucket {
  threshold: number;
  signals: number;
  wins: number;
  losses: number;
  pending: number;
  win_rate: number | null;
}

export interface AgeBucket {
  bucket: string;
  signals: number;
  wins: number;
  losses: number;
  pending: number;
  win_rate: number | null;
}

export interface Stats {
  total_signals: number;
  total_resolved: number;
  total_wins: number;
  total_losses: number;
  total_pending: number;
  by_threshold: ThresholdBucket[];
  by_age: AgeBucket[];
}
```

- [ ] **Step 2: Create DB client**

Create `dashboard/src/lib/db.ts`:

```typescript
import { neon } from "@neondatabase/serverless";

export function getDb() {
  if (!process.env.DATABASE_URL) {
    throw new Error("DATABASE_URL is not set");
  }
  return neon(process.env.DATABASE_URL);
}
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/lib/
git commit -m "feat: add DB client and TypeScript types for dashboard"
```

---

### Task 9: Dashboard Pages

**Files:**
- Modify: `dashboard/src/app/layout.tsx`
- Modify: `dashboard/src/app/page.tsx`

- [ ] **Step 1: Update root layout for dark mode**

Replace `dashboard/src/app/layout.tsx`:

```tsx
import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import "./globals.css";

export const metadata: Metadata = {
  title: "Whalebot Dashboard",
  description: "Paper trading stats for Polymarket whale signals",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className={`${GeistSans.variable} ${GeistMono.variable} font-sans antialiased bg-zinc-950 text-zinc-100 min-h-screen`}>
        <header className="border-b border-zinc-800 px-6 py-4">
          <div className="max-w-7xl mx-auto flex items-center justify-between">
            <h1 className="text-lg font-semibold tracking-tight">
              <span className="text-zinc-400">whalebot</span> dashboard
            </h1>
            <span className="text-xs text-zinc-500 font-mono">paper trading</span>
          </div>
        </header>
        <main className="max-w-7xl mx-auto px-6 py-8">
          {children}
        </main>
      </body>
    </html>
  );
}
```

Install Geist: `cd dashboard && npm install geist`

- [ ] **Step 2: Build the main dashboard page**

Replace `dashboard/src/app/page.tsx`:

```tsx
import { getDb } from "@/lib/db";
import type { Signal, ThresholdBucket, AgeBucket } from "@/lib/types";

export const dynamic = "force-dynamic";

const THRESHOLDS = [500, 1000, 2500, 5000, 10000, 25000, 50000, 100000];
const AGE_BUCKETS = [
  { label: "0-1d", min: 0, max: 1 },
  { label: "1-3d", min: 1, max: 3 },
  { label: "3-7d", min: 3, max: 7 },
  { label: "7d+", min: 7, max: 9999 },
];

async function getStats() {
  const sql = getDb();

  const totals = await sql`
    SELECT
      COUNT(*)::int as total,
      COUNT(*) FILTER (WHERE resolved AND won)::int as wins,
      COUNT(*) FILTER (WHERE resolved AND NOT won)::int as losses,
      COUNT(*) FILTER (WHERE NOT resolved)::int as pending
    FROM signals
  `;

  const byThreshold: ThresholdBucket[] = [];
  for (const threshold of THRESHOLDS) {
    const rows = await sql`
      SELECT
        COUNT(*)::int as signals,
        COUNT(*) FILTER (WHERE resolved AND won)::int as wins,
        COUNT(*) FILTER (WHERE resolved AND NOT won)::int as losses,
        COUNT(*) FILTER (WHERE NOT resolved)::int as pending
      FROM signals
      WHERE trade_size_usdc >= ${threshold}
    `;
    const r = rows[0];
    const resolved = r.wins + r.losses;
    byThreshold.push({
      threshold,
      signals: r.signals,
      wins: r.wins,
      losses: r.losses,
      pending: r.pending,
      win_rate: resolved > 0 ? r.wins / resolved : null,
    });
  }

  const byAge: AgeBucket[] = [];
  for (const { label, min, max } of AGE_BUCKETS) {
    const rows = await sql`
      SELECT
        COUNT(*)::int as signals,
        COUNT(*) FILTER (WHERE resolved AND won)::int as wins,
        COUNT(*) FILTER (WHERE resolved AND NOT won)::int as losses,
        COUNT(*) FILTER (WHERE NOT resolved)::int as pending
      FROM signals
      WHERE account_age_days >= ${min} AND account_age_days < ${max}
    `;
    const r = rows[0];
    const resolved = r.wins + r.losses;
    byAge.push({
      bucket: label,
      signals: r.signals,
      wins: r.wins,
      losses: r.losses,
      pending: r.pending,
      win_rate: resolved > 0 ? r.wins / resolved : null,
    });
  }

  const recent = await sql`
    SELECT id, created_at, wallet, trade_size_usdc, side,
           market_title, outcome, account_age_days, total_trades,
           entry_price, resolved, won, winning_outcome, market_slug
    FROM signals
    ORDER BY created_at DESC
    LIMIT 30
  `;

  return {
    total_signals: totals[0].total,
    total_wins: totals[0].wins,
    total_losses: totals[0].losses,
    total_pending: totals[0].pending,
    total_resolved: totals[0].wins + totals[0].losses,
    by_threshold: byThreshold,
    by_age: byAge,
    recent_signals: recent as Signal[],
  };
}

function formatUsd(n: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(n);
}

function WinRate({ rate }: { rate: number | null }) {
  if (rate === null) return <span className="text-zinc-500">N/A</span>;
  const pct = (rate * 100).toFixed(0);
  const color = rate >= 0.6 ? "text-green-400" : rate >= 0.5 ? "text-yellow-400" : "text-red-400";
  return <span className={color}>{pct}%</span>;
}

export default async function Dashboard() {
  const stats = await getStats();

  return (
    <div className="space-y-8">
      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        {[
          { label: "Total Signals", value: stats.total_signals },
          { label: "Resolved", value: stats.total_resolved },
          { label: "Wins", value: stats.total_wins, color: "text-green-400" },
          { label: "Losses", value: stats.total_losses, color: "text-red-400" },
          {
            label: "Win Rate",
            value: stats.total_resolved > 0
              ? `${(stats.total_wins / stats.total_resolved * 100).toFixed(0)}%`
              : "N/A",
            color: stats.total_resolved > 0 && stats.total_wins / stats.total_resolved >= 0.5
              ? "text-green-400" : "text-zinc-400",
          },
        ].map((card) => (
          <div key={card.label} className="rounded-lg border border-zinc-800 bg-zinc-900 p-4">
            <div className="text-xs text-zinc-500 mb-1">{card.label}</div>
            <div className={`text-2xl font-mono font-semibold ${card.color || "text-zinc-100"}`}>
              {card.value}
            </div>
          </div>
        ))}
      </div>

      {/* By Threshold */}
      <div className="rounded-lg border border-zinc-800 bg-zinc-900">
        <div className="px-4 py-3 border-b border-zinc-800">
          <h2 className="text-sm font-medium text-zinc-300">Win Rate by Trade Size</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-zinc-500 text-xs">
                <th className="px-4 py-2 text-left">Threshold</th>
                <th className="px-4 py-2 text-right">Signals</th>
                <th className="px-4 py-2 text-right">Wins</th>
                <th className="px-4 py-2 text-right">Losses</th>
                <th className="px-4 py-2 text-right">Pending</th>
                <th className="px-4 py-2 text-right">Win Rate</th>
              </tr>
            </thead>
            <tbody>
              {stats.by_threshold.map((b) => (
                <tr key={b.threshold} className="border-t border-zinc-800/50">
                  <td className="px-4 py-2 font-mono">{formatUsd(b.threshold)}+</td>
                  <td className="px-4 py-2 text-right font-mono">{b.signals}</td>
                  <td className="px-4 py-2 text-right font-mono text-green-400">{b.wins}</td>
                  <td className="px-4 py-2 text-right font-mono text-red-400">{b.losses}</td>
                  <td className="px-4 py-2 text-right font-mono text-zinc-500">{b.pending}</td>
                  <td className="px-4 py-2 text-right font-mono"><WinRate rate={b.win_rate} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* By Account Age */}
      <div className="rounded-lg border border-zinc-800 bg-zinc-900">
        <div className="px-4 py-3 border-b border-zinc-800">
          <h2 className="text-sm font-medium text-zinc-300">Win Rate by Account Age</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-zinc-500 text-xs">
                <th className="px-4 py-2 text-left">Age</th>
                <th className="px-4 py-2 text-right">Signals</th>
                <th className="px-4 py-2 text-right">Wins</th>
                <th className="px-4 py-2 text-right">Losses</th>
                <th className="px-4 py-2 text-right">Pending</th>
                <th className="px-4 py-2 text-right">Win Rate</th>
              </tr>
            </thead>
            <tbody>
              {stats.by_age.map((b) => (
                <tr key={b.bucket} className="border-t border-zinc-800/50">
                  <td className="px-4 py-2 font-mono">{b.bucket}</td>
                  <td className="px-4 py-2 text-right font-mono">{b.signals}</td>
                  <td className="px-4 py-2 text-right font-mono text-green-400">{b.wins}</td>
                  <td className="px-4 py-2 text-right font-mono text-red-400">{b.losses}</td>
                  <td className="px-4 py-2 text-right font-mono text-zinc-500">{b.pending}</td>
                  <td className="px-4 py-2 text-right font-mono"><WinRate rate={b.win_rate} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Recent Signals */}
      <div className="rounded-lg border border-zinc-800 bg-zinc-900">
        <div className="px-4 py-3 border-b border-zinc-800">
          <h2 className="text-sm font-medium text-zinc-300">Recent Signals</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-zinc-500 text-xs">
                <th className="px-4 py-2 text-left">Time</th>
                <th className="px-4 py-2 text-left">Market</th>
                <th className="px-4 py-2 text-left">Side</th>
                <th className="px-4 py-2 text-right">Size</th>
                <th className="px-4 py-2 text-right">Entry</th>
                <th className="px-4 py-2 text-right">Age</th>
                <th className="px-4 py-2 text-left">Status</th>
              </tr>
            </thead>
            <tbody>
              {stats.recent_signals.map((s) => {
                const status = s.resolved
                  ? s.won ? "WIN" : "LOSS"
                  : "PENDING";
                const statusColor = s.resolved
                  ? s.won ? "text-green-400" : "text-red-400"
                  : "text-zinc-500";
                const sideColor = s.side === "BUY" ? "text-green-400" : "text-red-400";

                return (
                  <tr key={s.id} className="border-t border-zinc-800/50">
                    <td className="px-4 py-2 font-mono text-zinc-400 text-xs whitespace-nowrap">
                      {new Date(s.created_at).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                    </td>
                    <td className="px-4 py-2 max-w-xs truncate">
                      {s.market_slug ? (
                        <a
                          href={`https://polymarket.com/event/${s.market_slug}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="hover:text-blue-400 transition-colors"
                        >
                          {s.market_title}
                        </a>
                      ) : (
                        s.market_title
                      )}
                      <span className="text-zinc-500 ml-1">({s.outcome})</span>
                    </td>
                    <td className={`px-4 py-2 font-mono ${sideColor}`}>{s.side}</td>
                    <td className="px-4 py-2 text-right font-mono">{formatUsd(s.trade_size_usdc)}</td>
                    <td className="px-4 py-2 text-right font-mono text-zinc-400">
                      {s.entry_price ? `$${s.entry_price.toFixed(2)}` : "—"}
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-zinc-400">
                      {s.account_age_days}d
                    </td>
                    <td className={`px-4 py-2 font-mono font-semibold ${statusColor}`}>
                      {status}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Test locally**

```bash
cd dashboard
echo "DATABASE_URL=your-neon-connection-string" > .env.local
npm run dev
```

Open http://localhost:3000 — should see the dashboard (empty stats initially).

- [ ] **Step 4: Commit**

```bash
cd /Users/tyleredwards/Documents/GitHub/whalebot
git add dashboard/src/
git commit -m "feat: add dashboard pages with stats tables and signal feed"
```

---

### Task 10: Deploy Dashboard to Vercel

- [ ] **Step 1: Set root directory in Vercel**

When connecting to Vercel, set the **Root Directory** to `dashboard/`.
The framework will be auto-detected as Next.js.

- [ ] **Step 2: Add environment variable**

In Vercel project settings, add:
- `DATABASE_URL` = your Neon connection string

- [ ] **Step 3: Deploy**

Push to GitHub or run `vercel` from `dashboard/` directory.

- [ ] **Step 4: Share URL with friend**

The Vercel preview URL is public by default — share it directly.

---

## Part 3: Mac Deployment

### Task 11: Launchd Service for Mac

**Files:**
- Create: `whalebot.plist`

- [ ] **Step 1: Create launchd plist**

Create `whalebot.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.whalebot.monitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/tyleredwards/Documents/GitHub/whalebot/venv/bin/python3</string>
        <string>-m</string>
        <string>src.main</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/tyleredwards/Documents/GitHub/whalebot</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/whalebot.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/whalebot.stderr.log</string>
</dict>
</plist>
```

Note: Update the python3 path to match `which python3` on the Mac. If using a venv, point to the venv's python.

- [ ] **Step 2: Install and start**

```bash
cp whalebot.plist ~/Library/LaunchAgents/com.whalebot.monitor.plist
launchctl load ~/Library/LaunchAgents/com.whalebot.monitor.plist
```

- [ ] **Step 3: Verify it's running**

```bash
launchctl list | grep whalebot
tail -f /tmp/whalebot.stdout.log
```

- [ ] **Step 4: Commit**

```bash
git add whalebot.plist
git commit -m "feat: add launchd plist for Mac deployment"
```

