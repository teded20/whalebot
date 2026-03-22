"""Polymarket contract addresses and ABIs on Polygon mainnet."""

# === Contract Addresses (from docs.polymarket.com/resources/contract-addresses) ===

CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
CONDITIONAL_TOKENS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
PROXY_FACTORY = "0xaB45c5A4B0c941a2F231C04C3f49182e1A254052"

# Addresses to ignore when analyzing wallets — these are Polymarket
# infrastructure contracts, not user accounts.
KNOWN_CONTRACTS: set[str] = {
    CTF_EXCHANGE.lower(),
    NEG_RISK_CTF_EXCHANGE.lower(),
    CONDITIONAL_TOKENS.lower(),
    USDC_E.lower(),
    NEG_RISK_ADAPTER.lower(),
    PROXY_FACTORY.lower(),
}

# USDC.e has 6 decimals
USDC_DECIMALS = 6

# === Minimal ABIs — only the events we care about ===

ORDER_FILLED_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "bytes32", "name": "orderHash", "type": "bytes32"},
            {"indexed": True, "internalType": "address", "name": "maker", "type": "address"},
            {"indexed": True, "internalType": "address", "name": "taker", "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "makerAssetId", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "takerAssetId", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "makerAmountFilled", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "takerAmountFilled", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "fee", "type": "uint256"},
        ],
        "name": "OrderFilled",
        "type": "event",
    }
]

ORDERS_MATCHED_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "bytes32", "name": "takerOrderHash", "type": "bytes32"},
            {"indexed": True, "internalType": "address", "name": "takerOrderMaker", "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "makerAssetId", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "takerAssetId", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "makerAmountFilled", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "takerAmountFilled", "type": "uint256"},
        ],
        "name": "OrdersMatched",
        "type": "event",
    }
]

# Combined ABI for creating contract instances
EXCHANGE_ABI = ORDER_FILLED_ABI + ORDERS_MATCHED_ABI
