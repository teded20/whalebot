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
