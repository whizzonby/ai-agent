"""
Setup Allowances: One-time script to approve Polymarket exchange contracts.

YOU MUST RUN THIS BEFORE THE AGENT CAN TRADE.

Uses eth-account (already installed by py-clob-client) + raw JSON-RPC.
No web3 dependency needed.

Usage:
    python setup_allowances.py

Requires a small amount of MATIC in your wallet for gas fees (~$0.01).
"""

import sys
import httpx
import structlog
from eth_account import Account
from eth_account.signers.local import LocalAccount
from config import config
from balance import (
    USDC_E_ADDRESS,
    USDC_NATIVE_ADDRESS,
    CTF_EXCHANGE,
    NEG_RISK_CTF_EXCHANGE,
    NEG_RISK_ADAPTER,
    POLYGON_RPC_URLS,
    get_usdc_balance,
    get_matic_balance,
    check_allowances,
    _get_wallet_address,
    _encode_address,
)

log = structlog.get_logger()

MAX_UINT256 = 2**256 - 1
# approve(address,uint256) selector
APPROVE_SELECTOR = "0x095ea7b3"


def _rpc_post(method: str, params: list) -> dict:
    """Send JSON-RPC request."""
    for url in POLYGON_RPC_URLS:
        try:
            resp = httpx.post(
                url,
                json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                if "error" not in data:
                    return data
                else:
                    print(f"  RPC error: {data['error']}")
        except Exception:
            continue
    raise ConnectionError("Could not connect to Polygon RPC")


def approve_token(account: LocalAccount, token_address: str, spender: str, label: str):
    """Send an ERC20 approve transaction."""
    address = _get_wallet_address()

    # Check current allowance first
    from balance import _eth_call, ALLOWANCE_SELECTOR
    call_data = ALLOWANCE_SELECTOR + _encode_address(address) + _encode_address(spender)
    result = _eth_call(token_address, call_data)
    current_allowance = int(result, 16)

    if current_allowance > 2**128:
        print(f"  ✅ {label} — already approved")
        return

    # Build approve(spender, MAX_UINT256) transaction data
    amount_hex = hex(MAX_UINT256)[2:].zfill(64)
    tx_data = APPROVE_SELECTOR + _encode_address(spender) + amount_hex

    # Get nonce
    nonce_result = _rpc_post("eth_getTransactionCount", [account.address, "latest"])
    nonce = int(nonce_result["result"], 16)

    # Get gas price
    gas_result = _rpc_post("eth_gasPrice", [])
    gas_price = int(gas_result["result"], 16)

    # Build transaction
    tx = {
        "nonce": nonce,
        "gasPrice": gas_price,
        "gas": 60000,
        "to": bytes.fromhex(token_address.replace("0x", "")),
        "value": 0,
        "data": bytes.fromhex(tx_data.replace("0x", "")),
        "chainId": config.chain_id,
    }

    # Sign
    signed = account.sign_transaction(tx)

    # Send
    raw_tx = "0x" + signed.raw_transaction.hex()
    send_result = _rpc_post("eth_sendRawTransaction", [raw_tx])
    tx_hash = send_result.get("result", "")

    if not tx_hash:
        print(f"  ❌ {label} — send failed: {send_result}")
        return

    print(f"  ⏳ {label} — tx sent: {tx_hash}")

    # Wait for receipt
    import time
    for _ in range(60):
        time.sleep(2)
        try:
            receipt_result = _rpc_post("eth_getTransactionReceipt", [tx_hash])
            receipt = receipt_result.get("result")
            if receipt:
                status = int(receipt.get("status", "0x0"), 16)
                if status == 1:
                    print(f"  ✅ {label} — approved!")
                else:
                    print(f"  ❌ {label} — transaction reverted")
                return
        except Exception:
            pass

    print(f"  ⚠️  {label} — timeout waiting for receipt (tx may still confirm)")


def main():
    config.validate()

    print("=" * 60)
    print("Polymarket Agent — Token Allowance Setup")
    print("=" * 60)

    account = Account.from_key(config.private_key)
    address = _get_wallet_address()

    print(f"\n🔑 Signer: {account.address}")
    print(f"📦 Funder: {address}")

    matic = get_matic_balance()
    print(f"⛽ MATIC balance: {matic:.4f}")

    if matic < 0.005:
        print("❌ Not enough MATIC for gas! Need at least 0.005 MATIC.")
        print("   Send some MATIC/POL to your wallet on Polygon.")
        sys.exit(1)

    usdc = get_usdc_balance()
    print(f"💰 USDC balance: ${usdc:.2f}")

    print("\n📝 Setting token allowances...\n")

    approvals = [
        (USDC_E_ADDRESS, CTF_EXCHANGE, "USDC.e → CTF Exchange"),
        (USDC_E_ADDRESS, NEG_RISK_CTF_EXCHANGE, "USDC.e → Neg Risk CTF Exchange"),
        (USDC_E_ADDRESS, NEG_RISK_ADAPTER, "USDC.e → Neg Risk Adapter"),
        (USDC_NATIVE_ADDRESS, CTF_EXCHANGE, "USDC → CTF Exchange"),
        (USDC_NATIVE_ADDRESS, NEG_RISK_CTF_EXCHANGE, "USDC → Neg Risk CTF Exchange"),
        (USDC_NATIVE_ADDRESS, NEG_RISK_ADAPTER, "USDC → Neg Risk Adapter"),
    ]

    for token, spender, label in approvals:
        try:
            approve_token(account, token, spender, label)
        except Exception as e:
            print(f"  ❌ {label} — error: {e}")

    print("\n✅ Done! Verifying allowances...\n")
    allowances = check_allowances()
    all_ok = True
    for pair, approved in allowances.items():
        status = "✅" if approved else "❌"
        print(f"  {status} {pair}")
        if not approved:
            all_ok = False

    if all_ok:
        print("\n🚀 All allowances set! You can now run: python main.py")
    else:
        print("\n⚠️  Some allowances failed. Check errors above.")


if __name__ == "__main__":
    main()