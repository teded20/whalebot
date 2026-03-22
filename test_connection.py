"""Test script: connect to Polygon, fetch recent OrderFilled events, print raw data.

Uses the public https://polygon-rpc.com endpoint (no API key needed).
Fetches the last 5 blocks of OrderFilled events from both CTF Exchange
and NegRisk CTF Exchange to validate ABI decoding and inspect field values.
"""

from web3 import Web3

from src.contracts import (
    CTF_EXCHANGE,
    NEG_RISK_CTF_EXCHANGE,
    EXCHANGE_ABI,
    USDC_DECIMALS,
)

RPC_URL = "https://polygon.drpc.org"


def main():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))

    if not w3.is_connected():
        print("ERROR: Cannot connect to Polygon RPC")
        return

    latest = w3.eth.block_number
    print(f"Connected to Polygon. Latest block: {latest}")
    print()

    # We'll scan a wider range to increase chances of finding events.
    # Polygon produces blocks every ~2s, so 500 blocks = ~16 minutes.
    from_block = latest - 500
    to_block = latest

    for addr, name in [
        (CTF_EXCHANGE, "CTF Exchange"),
        (NEG_RISK_CTF_EXCHANGE, "NegRisk CTF Exchange"),
    ]:
        print(f"{'='*70}")
        print(f"Scanning {name}: {addr}")
        print(f"Blocks {from_block} - {to_block} ({to_block - from_block} blocks)")
        print(f"{'='*70}")

        contract = w3.eth.contract(
            address=Web3.to_checksum_address(addr),
            abi=EXCHANGE_ABI,
        )

        try:
            events = contract.events.OrderFilled.get_logs(
                from_block=from_block,
                to_block=to_block,
            )
        except Exception as e:
            print(f"  Error fetching events: {e}")
            print()
            continue

        print(f"  Found {len(events)} OrderFilled events")
        print()

        # Print up to 5 events with full details
        for i, event in enumerate(events[:5]):
            args = event["args"]
            tx_hash = event["transactionHash"].hex()
            block = event["blockNumber"]

            maker_amount_usdc = args["makerAmountFilled"] / (10**USDC_DECIMALS)
            taker_amount_usdc = args["takerAmountFilled"] / (10**USDC_DECIMALS)

            print(f"  --- Event {i+1} (block {block}, tx {tx_hash[:16]}...) ---")
            print(f"  maker:             {args['maker']}")
            print(f"  taker:             {args['taker']}")
            print(f"  makerAssetId:      {args['makerAssetId']}")
            print(f"  takerAssetId:      {args['takerAssetId']}")
            print(f"  makerAmountFilled: {args['makerAmountFilled']}  (as USDC: ${maker_amount_usdc:,.2f})")
            print(f"  takerAmountFilled: {args['takerAmountFilled']}  (as USDC: ${taker_amount_usdc:,.2f})")
            print(f"  fee:               {args['fee']}  (as USDC: ${args['fee'] / (10**USDC_DECIMALS):,.4f})")
            print()

            # Show which side looks like USDC vs CTF token
            maker_id = args["makerAssetId"]
            taker_id = args["takerAssetId"]
            print(f"  Analysis:")
            print(f"    makerAssetId digits: {len(str(maker_id))}")
            print(f"    takerAssetId digits: {len(str(taker_id))}")
            if maker_id == 0:
                print(f"    -> makerAssetId is 0 (likely USDC side)")
                print(f"    -> Trade: maker BUYs CTF tokens, pays ${maker_amount_usdc:,.2f} USDC")
            elif taker_id == 0:
                print(f"    -> takerAssetId is 0 (likely USDC side)")
                print(f"    -> Trade: maker SELLs CTF tokens, receives ${taker_amount_usdc:,.2f} USDC")
            else:
                print(f"    -> Neither assetId is 0. Both are non-zero:")
                print(f"       makerAssetId: {maker_id}")
                print(f"       takerAssetId: {taker_id}")
                # Check if they look like CTF token IDs (very large numbers)
                if maker_id > 10**15 and taker_id > 10**15:
                    print(f"    -> BOTH look like CTF token IDs! USDC detection heuristic may be wrong.")
                elif maker_id > 10**15:
                    print(f"    -> makerAssetId looks like CTF token, takerAssetId might be USDC side")
                elif taker_id > 10**15:
                    print(f"    -> takerAssetId looks like CTF token, makerAssetId might be USDC side")
            print()

        if len(events) > 5:
            print(f"  ... and {len(events) - 5} more events")
        print()


if __name__ == "__main__":
    main()
