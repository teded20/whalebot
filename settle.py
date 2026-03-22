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
