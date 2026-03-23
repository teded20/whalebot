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

        if row and row["wallet_count"] >= 3:
            # Check if wallets share a funding source
            shared_source = None
            wallet_list = row["wallets"]
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
                    shared_source = source_row["funding_source"]

            return {
                "wallet_count": row["wallet_count"],
                "total_volume_usdc": float(row["total_volume"] or 0),
                "wallets": wallet_list,
                "avg_suspicion_score": float(row["avg_score"]) if row["avg_score"] else None,
                "shared_funding_source": shared_source,
                "time_window_minutes": 120,
            }

    return None
