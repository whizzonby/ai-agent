"""
Setup Allowances: One-time script to approve Polymarket exchange contracts.

YOU MUST RUN THIS BEFORE THE AGENT CAN TRADE.

This approves the Polymarket CTF Exchange, Neg Risk CTF Exchange, and
Neg Risk Adapter contracts to spend your USDC for trading.

Usage:
    python setup_allowances.py

Requires a small amount of MATIC in your wallet for gas fees (~$0.01).
"""

import sys
from web3 import Web3
from config import config
from balance import (
    USDC_E_ADDRESS,
    USDC_NATIVE_ADDRESS,
    CTF_EXCHANGE,
    NEG_RISK_CTF_EXCHANGE,
    NEG_RISK_ADAPTER,
    ERC20_ABI,
    _get_web3,
    _get_wallet_address,
    get_usdc_balance,
    check_allowances,
)

MAX_UINT256 = 2**256 - 1


def approve_token(w3: Web3, token_address: str, spender: str, label: str):
    """Send an approval transaction."""
    account = w3.eth.account.from_key(config.private_key)
    address = _get_wallet_address()

    contract = w3.eth.contract(
        address=Web3.to_checksum_address(token_address),
        abi=ERC20_ABI,
    )

    # Check current allowance
    current = contract.functions.allowance(
        address, Web3.to_checksum_address(spender)
    ).call()

    if current > MAX_UINT256 // 2:
        print(f"  ✅ {label} — already approved")
        return

    # Build approval transaction
    nonce = w3.eth.get_transaction_count(address)
    gas_price = w3.eth.gas_price

    tx = contract.functions.approve(
        Web3.to_checksum_address(spender), MAX_UINT256
    ).build_transaction({
        "from": address,
        "nonce": nonce,
        "gasPrice": gas_price,
        "gas": 60000,
        "chainId": config.chain_id,
    })

    # Sign and send
    signed = w3.eth.account.sign_transaction(tx, config.private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

    if receipt.status == 1:
        print(f"  ✅ {label} — approved (tx: {tx_hash.hex()})")
    else:
        print(f"  ❌ {label} — transaction failed!")
        sys.exit(1)


def main():
    config.validate()

    print("=" * 60)
    print("Polymarket Agent — Token Allowance Setup")
    print("=" * 60)

    w3 = _get_web3()
    address = _get_wallet_address()

    # Check MATIC balance for gas
    matic_balance = w3.eth.get_balance(address)
    matic_usd_approx = (matic_balance / 1e18) * 0.40  # rough MATIC price
    print(f"\n🔑 Wallet: {address}")
    print(f"⛽ MATIC balance: {matic_balance / 1e18:.4f} (~${matic_usd_approx:.2f})")

    if matic_balance < Web3.to_wei(0.01, "ether"):
        print("❌ Not enough MATIC for gas! Need at least 0.01 MATIC.")
        print("   Send some MATIC to your wallet on Polygon.")
        sys.exit(1)

    usdc = get_usdc_balance()
    print(f"💰 USDC balance: ${usdc:.2f}")

    # Approve all exchange contracts for both USDC variants
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
            approve_token(w3, token, spender, label)
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
        print("\n⚠️  Some allowances failed. Check the errors above.")


if __name__ == "__main__":
    main()
