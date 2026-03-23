"""Trace wallet funding sources on Polygon.

Looks at the first USDC transfer into a wallet to determine where
the funds came from — exchange hot wallet, another user wallet, etc.
"""

import logging
from datetime import datetime, timezone

import httpx

from .config import config

logger = logging.getLogger(__name__)

# Known exchange hot wallets on Polygon (USDC transfers)
KNOWN_EXCHANGES = {
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "Binance",
    "0xe7804c37c13166ff0b37f5ae0bb07a3aebb6e245": "Binance",
    "0x6cc68acf01a754ef9a82b1ee0822b88e52559431": "Coinbase",
    "0x0d0707963952f2fba59dd06f2b425ace40b492fe": "Gate.io",
    "0x28c6c06298d514db089934071355e5743bf21d60": "Binance",
    "0x1ab4973a48dc892cd9971ece8e01dcc7688f8f23": "Bybit",
    "0xf89d7b9c864f589bbf53a82105107622b35eaa40": "Bybit",
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
        for usdc_addr in [USDC_POLYGON, USDC_BRIDGED]:
            params = {
                "module": "account",
                "action": "tokentx",
                "contractaddress": usdc_addr,
                "address": wallet,
                "page": "1",
                "offset": "10",
                "sort": "asc",
            }
            if config.polygonscan_api_key:
                params["apikey"] = config.polygonscan_api_key

            resp = await client.get(POLYGONSCAN_API, params=params, timeout=10)
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
