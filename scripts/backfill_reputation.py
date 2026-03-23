"""One-time backfill of wallet_reputation from existing signals."""
import asyncio
import os
from dotenv import load_dotenv
import psycopg

load_dotenv()

async def backfill():
    conn = await psycopg.AsyncConnection.connect(os.environ["DATABASE_URL"])
    async with conn:
        # Ensure wallet_reputation table exists (mirrors database.py schema)
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
            )
        """)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wallet_rep_wins ON wallet_reputation(total_wins)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wallet_rep_streak ON wallet_reputation(suspicion_streak)"
        )

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

        # Compute win streaks from most recent resolved signals
        rows = await conn.execute("""
            SELECT LOWER(wallet) as wallet, won, created_at
            FROM signals
            WHERE resolved = true
            ORDER BY LOWER(wallet), created_at DESC
        """)
        streaks = {}
        for row in await rows.fetchall():
            w = row[0]
            won = row[1]
            if w not in streaks:
                streaks[w] = 0
            if won and streaks[w] >= 0:
                streaks[w] += 1
            else:
                streaks[w] = -1  # streak broken

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
