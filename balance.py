"""
Balance: Query USDC balance on Polygon for the agent's wallet.

Uses web3.py to check on-chain USDC balance.
Also provides a one-time setup script for token allowances.
"""

import structlog
from web3 import Web3
from config import config

log = structlog.get_logger()

# Polygon USDC contract (bridged USDC.e and native USDC)
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e (6 decimals)
USDC_NATIVE_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"  # Native USDC (6 decimals)

# Polymarket exchange contracts (for allowance approval)
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

# Minimal ERC20 ABI for balanceOf and approve
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
]

# Free Polygon RPC endpoints (use your own for production reliability)
POLYGON_RPC_URLS = [
    "https://polygon-rpc.com",
    "https://rpc-mainnet.matic.quiknode.pro",
    "https://polygon.llamarpc.com",
]


def _get_web3() -> Web3:
    """Connect to Polygon RPC."""
    for rpc_url in POLYGON_RPC_URLS:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 10}))
            if w3.is_connected():
                return w3
        except Exception:
            continue
    raise ConnectionError("Could not connect to any Polygon RPC endpoint")


def _get_wallet_address() -> str:
    """Derive wallet address from private key.

    If funder_address is set (proxy wallet), use that.
    Otherwise derive from private key.
    """
    if config.funder_address:
        return Web3.to_checksum_address(config.funder_address)

    w3 = _get_web3()
    account = w3.eth.account.from_key(config.private_key)
    return account.address


def get_usdc_balance() -> float:
    """Get total USDC balance (USDC.e + native USDC) in USD terms."""
    w3 = _get_web3()
    address = _get_wallet_address()
    total = 0.0

    for usdc_addr in [USDC_E_ADDRESS, USDC_NATIVE_ADDRESS]:
        try:
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(usdc_addr),
                abi=ERC20_ABI,
            )
            raw_balance = contract.functions.balanceOf(address).call()
            # USDC has 6 decimals
            balance = raw_balance / 1e6
            total += balance
        except Exception as e:
            log.warning("balance.usdc_check_failed", contract=usdc_addr, error=str(e))

    log.info("balance.checked", address=address, usdc_balance=f"${total:.2f}")
    return total


def check_allowances() -> dict[str, bool]:
    """Check if token allowances are set for Polymarket exchange contracts."""
    w3 = _get_web3()
    address = _get_wallet_address()

    results = {}
    max_uint = 2**256 - 1
    threshold = max_uint // 2  # consider "approved" if allowance > half of max

    for usdc_addr_label, usdc_addr in [("USDC.e", USDC_E_ADDRESS), ("USDC", USDC_NATIVE_ADDRESS)]:
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(usdc_addr),
            abi=ERC20_ABI,
        )
        for exchange_label, exchange_addr in [
            ("CTF_EXCHANGE", CTF_EXCHANGE),
            ("NEG_RISK_CTF", NEG_RISK_CTF_EXCHANGE),
            ("NEG_RISK_ADAPTER", NEG_RISK_ADAPTER),
        ]:
            try:
                allowance = contract.functions.allowance(
                    address, Web3.to_checksum_address(exchange_addr)
                ).call()
                approved = allowance > threshold
                key = f"{usdc_addr_label}->{exchange_label}"
                results[key] = approved
                if not approved:
                    log.warning("allowance.not_set", pair=key)
            except Exception as e:
                results[f"{usdc_addr_label}->{exchange_label}"] = False
                log.error("allowance.check_failed", error=str(e))

    return results


if __name__ == "__main__":
    """Run standalone to check balance and allowances."""
    print("=" * 50)
    print("Polymarket Agent - Wallet Check")
    print("=" * 50)

    try:
        balance = get_usdc_balance()
        print(f"\n💰 USDC Balance: ${balance:.2f}")

        print("\n🔐 Token Allowances:")
        allowances = check_allowances()
        for pair, approved in allowances.items():
            status = "✅" if approved else "❌ NOT SET"
            print(f"   {pair}: {status}")

        if not all(allowances.values()):
            print("\n⚠️  Some allowances are not set!")
            print("   You need to approve the Polymarket exchange contracts.")
            print("   See: https://github.com/Polymarket/py-clob-client#allowances")
            print("   Or run: python setup_allowances.py")

    except Exception as e:
        print(f"\n❌ Error: {e}")
