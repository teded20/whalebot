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
    -- Suspicion scoring
    suspicion_score INTEGER NOT NULL DEFAULT 0,
    score_tier      TEXT NOT NULL DEFAULT 'LOW',
    score_breakdown TEXT NOT NULL DEFAULT '{}',
    unique_markets  INTEGER NOT NULL DEFAULT 0,
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

        # Migrate: add scoring columns if they don't exist yet
        for col, defn in [
            ("suspicion_score", "INTEGER NOT NULL DEFAULT 0"),
            ("score_tier", "TEXT NOT NULL DEFAULT 'LOW'"),
            ("score_breakdown", "TEXT NOT NULL DEFAULT '{}'"),
            ("unique_markets", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            try:
                await conn.execute(
                    f"ALTER TABLE signals ADD COLUMN IF NOT EXISTS {col} {defn}"
                )
            except Exception:
                pass  # Column already exists

        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_score ON signals(suspicion_score)"
        )

        # Add hours_to_resolution column
        try:
            await conn.execute("""
                ALTER TABLE signals ADD COLUMN IF NOT EXISTS
                    hours_to_resolution DOUBLE PRECISION
            """)
        except Exception:
            pass

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
                    entry_price, pseudonym, condition_id, market_slug,
                    suspicion_score, score_tier, score_breakdown, unique_markets,
                    hours_to_resolution
                ) VALUES (
                    %(wallet)s, %(trade_size_usdc)s, %(side)s, %(ctf_token_id)s,
                    %(market_title)s, %(outcome)s, %(exchange)s, %(tx_hash)s,
                    %(account_age_days)s, %(total_trades)s, %(total_volume_usdc)s,
                    %(entry_price)s, %(pseudonym)s, %(condition_id)s, %(market_slug)s,
                    %(suspicion_score)s, %(score_tier)s, %(score_breakdown)s, %(unique_markets)s,
                    %(hours_to_resolution)s
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


async def count_recent_new_wallets_on_outcome(
    ctf_token_id: str, exclude_wallet: str, hours: int = 24
) -> int:
    """Count other new-wallet signals on the same outcome in the last N hours.

    This detects cluster behavior: multiple fresh wallets piling into
    the same outcome is a strong insider signal (Iran strikes: 6 wallets,
    OpenAI launches: 13 wallets, Axiom: 12 wallets).
    """
    if _pool is None:
        return 0

    try:
        async with _pool.connection() as conn:
            row = await (await conn.execute(
                """
                SELECT COUNT(*) as cnt FROM signals
                WHERE ctf_token_id = %s
                  AND LOWER(wallet) != LOWER(%s)
                  AND account_age_days <= 7
                  AND created_at > NOW() - INTERVAL '1 hour' * %s
                """,
                (ctf_token_id, exclude_wallet, hours),
            )).fetchone()
            return row["cnt"] if row else 0
    except Exception as e:
        logger.error(f"Failed to count cluster signals: {e}")
        return 0


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
        await conn.commit()
        return dict(await row.fetchone())


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
        await conn.commit()


async def get_wallet_reputation(wallet: str) -> dict | None:
    """Get wallet reputation. Returns None if wallet not seen before."""
    async with _pool.connection() as conn:
        row = await conn.execute("""
            SELECT * FROM wallet_reputation WHERE wallet = %(wallet)s
        """, {"wallet": wallet.lower()})
        result = await row.fetchone()
        return dict(result) if result else None


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
        await conn.commit()


async def count_wallets_from_same_source(funding_source: str) -> int:
    """Count how many tracked wallets were funded by the same address."""
    if not funding_source:
        return 0
    async with _pool.connection() as conn:
        row = await conn.execute("""
            SELECT COUNT(*) as cnt FROM wallet_funding
            WHERE funding_source = %(source)s
        """, {"source": funding_source.lower()})
        result = await row.fetchone()
        return result["cnt"] if result else 0


async def save_wave_event(condition_id: str, outcome: str, wave: dict) -> None:
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
        await conn.commit()
