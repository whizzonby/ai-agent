"""
Balance: Query USDC balance on Polygon for the agent's wallet.

Uses raw JSON-RPC calls via httpx (no web3 dependency needed).
Also checks token allowances for Polymarket exchange contracts.
"""

import httpx
import structlog
from eth_account import Account
from config import config

log = structlog.get_logger()

# Polygon USDC contracts (6 decimals)
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC_NATIVE_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"

# Polymarket exchange contracts
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

# ERC20 function selectors (keccak256 of function signature, first 4 bytes)
# balanceOf(address) -> 0x70a08231
# allowance(address,address) -> 0xdd62ed3e
BALANCE_OF_SELECTOR = "0x70a08231"
ALLOWANCE_SELECTOR = "0xdd62ed3e"

POLYGON_RPC_URLS = [
    "https://polygon-rpc.com",
    "https://polygon.llamarpc.com",
    "https://rpc.ankr.com/polygon",
]


def _rpc_call(method: str, params: list, rpc_url: str = None) -> dict:
    """Make a raw JSON-RPC call to Polygon."""
    urls = [rpc_url] if rpc_url else POLYGON_RPC_URLS

    for url in urls:
        try:
            resp = httpx.post(
                url,
                json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                if "error" not in data:
                    return data
        except Exception:
            continue

    raise ConnectionError("Could not connect to any Polygon RPC endpoint")


def _eth_call(to: str, data: str) -> str:
    """Execute eth_call and return the hex result."""
    result = _rpc_call("eth_call", [{"to": to, "data": data}, "latest"])
    return result.get("result", "0x0")


def _encode_address(addr: str) -> str:
    """ABI-encode an address (pad to 32 bytes)."""
    return addr.lower().replace("0x", "").zfill(64)


def _get_wallet_address() -> str:
    """Derive wallet address from private key or use funder address."""
    if config.funder_address:
        return config.funder_address

    account = Account.from_key(config.private_key)
    return account.address


def get_usdc_balance() -> float:
    """Get total USDC balance (USDC.e + native USDC) in USD terms."""
    address = _get_wallet_address()
    total = 0.0

    for usdc_addr in [USDC_E_ADDRESS, USDC_NATIVE_ADDRESS]:
        try:
            # balanceOf(address) call
            call_data = BALANCE_OF_SELECTOR + _encode_address(address)
            result = _eth_call(usdc_addr, call_data)
            raw_balance = int(result, 16)
            balance = raw_balance / 1e6  # USDC has 6 decimals
            total += balance
        except Exception as e:
            log.warning("balance.usdc_check_failed", contract=usdc_addr, error=str(e))

    log.info("balance.checked", address=address, usdc_balance=f"${total:.2f}")
    return total


def check_allowances() -> dict[str, bool]:
    """Check if token allowances are set for Polymarket exchange contracts."""
    address = _get_wallet_address()
    results = {}
    threshold = 2**128  # consider "approved" if allowance > this

    for usdc_label, usdc_addr in [("USDC.e", USDC_E_ADDRESS), ("USDC", USDC_NATIVE_ADDRESS)]:
        for exchange_label, exchange_addr in [
            ("CTF_EXCHANGE", CTF_EXCHANGE),
            ("NEG_RISK_CTF", NEG_RISK_CTF_EXCHANGE),
            ("NEG_RISK_ADAPTER", NEG_RISK_ADAPTER),
        ]:
            key = f"{usdc_label}->{exchange_label}"
            try:
                # allowance(owner, spender) call
                call_data = (
                    ALLOWANCE_SELECTOR
                    + _encode_address(address)
                    + _encode_address(exchange_addr)
                )
                result = _eth_call(usdc_addr, call_data)
                allowance_val = int(result, 16)
                approved = allowance_val > threshold
                results[key] = approved
                if not approved:
                    log.warning("allowance.not_set", pair=key)
            except Exception as e:
                results[key] = False
                log.error("allowance.check_failed", pair=key, error=str(e))

    return results


def get_matic_balance() -> float:
    """Get MATIC (POL) balance for gas."""
    address = _get_wallet_address()
    try:
        result = _rpc_call("eth_getBalance", [address, "latest"])
        raw = int(result.get("result", "0x0"), 16)
        return raw / 1e18
    except Exception as e:
        log.warning("balance.matic_failed", error=str(e))
        return 0.0


if __name__ == "__main__":
    """Run standalone to check balance and allowances."""
    print("=" * 50)
    print("Polymarket Agent - Wallet Check")
    print("=" * 50)

    try:
        address = _get_wallet_address()
        print(f"\n🔑 Wallet: {address}")

        matic = get_matic_balance()
        print(f"⛽ MATIC balance: {matic:.4f}")

        balance = get_usdc_balance()
        print(f"💰 USDC Balance: ${balance:.2f}")

        print("\n🔐 Token Allowances:")
        allowances = check_allowances()
        for pair, approved in allowances.items():
            status = "✅" if approved else "❌ NOT SET"
            print(f"   {pair}: {status}")

        if not all(allowances.values()):
            print("\n⚠️  Some allowances are not set!")
            print("   Run: python setup_allowances.py")

    except Exception as e:
        print(f"\n❌ Error: {e}")