"""
Executor: Places trades on Polymarket via the CLOB API.

Uses py-clob-client for order signing and submission.

Key API facts (from Polymarket docs):
- MarketOrderArgs: token_id, amount (USD), side (BUY/SELL)
  -> does NOT take order_type in constructor
- create_market_order(args) -> signed order
- post_order(signed_order, OrderType.FOK) -> response dict
- get_order_book(token_id) -> OrderBookSummary object with .asks, .bids
- OrderBookSummary.asks/bids are lists of OrderSummary with .price, .size
- Price must be between 0.01 and 0.99
"""

import structlog
from dataclasses import dataclass
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    MarketOrderArgs,
    OrderArgs,
    OrderType,
    BookParams,
    ApiCreds,
)
from py_clob_client.order_builder.constants import BUY, SELL
from position_sizer import TradeSignal
from config import config

log = structlog.get_logger()


@dataclass
class ExecutionResult:
    signal: TradeSignal
    success: bool
    order_id: str | None
    fill_price: float | None
    fill_amount: float | None
    error: str | None


class TradeExecutor:
    def __init__(self):
        # Build client kwargs — funder is optional (only for proxy wallets)
        kwargs = {
            "host": config.clob_url,
            "key": config.private_key,
            "chain_id": config.chain_id,
            "signature_type": config.signature_type,
        }
        if config.funder_address:
            kwargs["funder"] = config.funder_address

        self.client = ClobClient(**kwargs)

        # Derive API credentials (deterministic from private key — only need to do once)
        try:
            creds = self.client.create_or_derive_api_creds()
            self.client.set_api_creds(creds)
            log.info("executor.initialized", msg="API credentials set")
        except Exception as e:
            log.error("executor.creds_failed", error=str(e))
            raise

    def execute(self, signal: TradeSignal) -> ExecutionResult:
        """Execute a single trade signal as a Fill-or-Kill market order."""

        log.info(
            "executor.placing_order",
            question=signal.estimate.market.question[:50],
            side=signal.side,
            size=f"${signal.position_size_usd:.2f}",
            token=signal.token_id[:20] + "...",
        )

        try:
            # ----------------------------------------------------------
            # 1. Check order book depth and slippage
            # ----------------------------------------------------------
            book = self.client.get_order_book(signal.token_id)

            # book.asks is a list of OrderSummary objects with .price and .size
            if not book.asks:
                return ExecutionResult(
                    signal=signal, success=False, order_id=None,
                    fill_price=None, fill_amount=None,
                    error="No asks in order book — market may be illiquid",
                )

            best_ask = float(book.asks[0].price)

            # Slippage guard: reject if best ask is >5% above our expected entry
            max_acceptable = min(signal.entry_price * 1.05, 0.99)
            if best_ask > max_acceptable:
                return ExecutionResult(
                    signal=signal, success=False, order_id=None,
                    fill_price=None, fill_amount=None,
                    error=f"Slippage: best_ask={best_ask:.4f} > max={max_acceptable:.4f}",
                )

            # ----------------------------------------------------------
            # 2. Build and submit market order (FOK)
            # ----------------------------------------------------------
            # MarketOrderArgs for BUY: amount is in USD
            order_args = MarketOrderArgs(
                token_id=signal.token_id,
                amount=signal.position_size_usd,
                side=BUY,
            )

            signed_order = self.client.create_market_order(order_args)
            resp = self.client.post_order(signed_order, OrderType.FOK)

            # ----------------------------------------------------------
            # 3. Parse response
            # ----------------------------------------------------------
            # Response is typically: {"orderID": "...", "status": "matched", ...}
            # or {"success": true/false, ...}
            if isinstance(resp, dict):
                order_id = resp.get("orderID") or resp.get("id")
                status = resp.get("status", "")
                success = order_id is not None or status in ("matched", "filled")
                error_msg = None if success else f"Response: {str(resp)[:200]}"
            else:
                # Some versions return a string or other type
                order_id = str(resp) if resp else None
                success = resp is not None
                error_msg = None if success else "Empty response from post_order"

            result = ExecutionResult(
                signal=signal,
                success=success,
                order_id=order_id,
                fill_price=best_ask,  # approximate — actual fill may differ slightly
                fill_amount=signal.position_size_usd if success else None,
                error=error_msg,
            )

            if success:
                log.info(
                    "executor.filled",
                    order_id=order_id,
                    question=signal.estimate.market.question[:50],
                    size=f"${signal.position_size_usd:.2f}",
                    approx_price=f"{best_ask:.4f}",
                )
            else:
                log.warning("executor.rejected", error=error_msg)

            return result

        except Exception as e:
            error_str = str(e)
            log.error("executor.error", error=error_str, market=signal.estimate.market.slug)
            return ExecutionResult(
                signal=signal, success=False, order_id=None,
                fill_price=None, fill_amount=None, error=error_str,
            )

    def execute_batch(self, signals: list[TradeSignal]) -> list[ExecutionResult]:
        """Execute multiple trade signals sequentially.

        In production you could batch these, but sequential is safer
        for a small bankroll — you can abort if something goes wrong.
        """
        results = []
        for signal in signals:
            result = self.execute(signal)
            results.append(result)

            # If a trade fails due to auth/network, stop trying
            if not result.success and result.error and (
                "auth" in result.error.lower()
                or "network" in result.error.lower()
                or "rate" in result.error.lower()
            ):
                log.error("executor.batch_abort", reason=result.error[:100])
                break

        return results

    def get_midpoint(self, token_id: str) -> float | None:
        """Get midpoint price for a token."""
        try:
            mid = self.client.get_midpoint(token_id)
            return float(mid) if mid else None
        except Exception as e:
            log.warning("executor.midpoint_error", error=str(e))
            return None

    def check_connectivity(self) -> bool:
        """Verify CLOB API is reachable and credentials work."""
        try:
            resp = self.client.get_ok()
            return resp == "OK"
        except Exception as e:
            log.error("executor.connectivity_failed", error=str(e))
            return False
