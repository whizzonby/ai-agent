"""
Mispricing Detector + Kelly Criterion Position Sizer

Finds markets where Claude's fair value diverges from market price by >8%,
then calculates optimal position size using Kelly Criterion capped at 6% bankroll.
"""

import math
import structlog
from dataclasses import dataclass
from fair_value import FairValueEstimate
from config import config

log = structlog.get_logger()


@dataclass
class TradeSignal:
    """A concrete trade the agent wants to execute."""
    estimate: FairValueEstimate
    side: str               # "YES" or "NO"
    token_id: str           # which token to buy
    entry_price: float      # price we'd pay
    fair_price: float       # what we think it's worth
    edge: float             # fair - entry (always positive for valid signals)
    kelly_fraction: float   # raw Kelly fraction
    capped_fraction: float  # after applying max position cap
    position_size_usd: float  # dollar amount to risk
    expected_value: float   # edge * position_size


class MispricingDetector:
    """Filters estimates to find actionable mispricings."""

    def __init__(self, min_edge_pct: float = None):
        self.min_edge = (min_edge_pct or config.min_edge) / 100.0

    def find_signals(self, estimates: list[FairValueEstimate]) -> list[FairValueEstimate]:
        """Return estimates with absolute edge > threshold and sufficient confidence."""
        signals = []
        for est in estimates:
            if est.abs_edge >= self.min_edge and est.confidence >= 0.4:
                signals.append(est)
                log.info(
                    "mispricing.found",
                    question=est.market.question[:50],
                    edge=f"{est.abs_edge*100:.1f}%",
                    confidence=f"{est.confidence:.2f}",
                    side=est.recommended_side,
                )
        # Sort by edge * confidence (best opportunities first)
        signals.sort(key=lambda e: e.abs_edge * e.confidence, reverse=True)
        return signals


class KellyPositionSizer:
    """
    Kelly Criterion position sizing with conservative caps.

    Full Kelly: f* = (bp - q) / b
    where:
        b = odds received (net payout per $1 wagered)
        p = probability of winning
        q = 1 - p

    For prediction markets:
        - Buying YES at price P, payout is $1 if YES
        - b = (1 - P) / P  (net profit per dollar risked)
        - p = Claude's fair probability

    We use fractional Kelly (typically 1/4 to 1/2) for safety,
    and hard cap at max_position_pct of bankroll.
    """

    def __init__(
        self,
        bankroll: float,
        max_position_pct: float = None,
        kelly_fraction: float = 0.25,  # quarter-Kelly for safety
    ):
        self.bankroll = bankroll
        self.max_position_pct = (max_position_pct or config.max_position_pct) / 100.0
        self.kelly_fraction = kelly_fraction

    def size(self, estimate: FairValueEstimate) -> TradeSignal | None:
        """Calculate position size for a given mispricing."""

        if estimate.recommended_side == "YES":
            entry_price = estimate.market.yes_price
            fair_price = estimate.fair_yes_prob
            token_id = estimate.market.outcome_yes_token
        else:
            entry_price = estimate.market.no_price
            fair_price = 1.0 - estimate.fair_yes_prob
            token_id = estimate.market.outcome_no_token

        # Sanity checks
        if entry_price <= 0.01 or entry_price >= 0.99:
            return None
        if fair_price <= entry_price:
            return None  # No edge on this side

        # Kelly Criterion
        b = (1.0 - entry_price) / entry_price  # net odds
        p = fair_price                          # estimated win probability
        q = 1.0 - p

        kelly_raw = (b * p - q) / b

        if kelly_raw <= 0:
            return None  # Kelly says don't bet

        # Apply fractional Kelly and cap
        kelly_adjusted = kelly_raw * self.kelly_fraction

        # Scale by confidence (lower confidence = smaller bet)
        kelly_adjusted *= estimate.confidence

        capped = min(kelly_adjusted, self.max_position_pct)
        position_usd = capped * self.bankroll

        # Minimum trade size (avoid dust trades)
        if position_usd < 1.0:
            return None

        edge = fair_price - entry_price
        expected_value = edge * position_usd

        signal = TradeSignal(
            estimate=estimate,
            side=estimate.recommended_side,
            token_id=token_id,
            entry_price=entry_price,
            fair_price=fair_price,
            edge=edge,
            kelly_fraction=kelly_raw,
            capped_fraction=capped,
            position_size_usd=round(position_usd, 2),
            expected_value=round(expected_value, 2),
        )

        log.info(
            "position.sized",
            question=estimate.market.question[:50],
            side=signal.side,
            entry=f"{entry_price:.3f}",
            fair=f"{fair_price:.3f}",
            edge=f"{edge*100:.1f}%",
            kelly_raw=f"{kelly_raw:.3f}",
            kelly_capped=f"{capped:.3f}",
            size_usd=f"${position_usd:.2f}",
            ev=f"${expected_value:.2f}",
        )

        return signal

    def size_batch(self, estimates: list[FairValueEstimate]) -> list[TradeSignal]:
        """Size positions for all mispriced estimates.

        Also enforces total portfolio exposure limit:
        sum of all positions <= 50% of bankroll (diversification).
        """
        signals = []
        total_exposure = 0.0
        max_total_exposure = self.bankroll * 0.5  # never risk >50% of bankroll at once

        for est in estimates:
            if total_exposure >= max_total_exposure:
                log.info("position.max_exposure_reached", total=f"${total_exposure:.2f}")
                break

            signal = self.size(est)
            if signal:
                # Reduce size if it would exceed total exposure limit
                remaining = max_total_exposure - total_exposure
                if signal.position_size_usd > remaining:
                    signal.position_size_usd = round(remaining, 2)
                    signal.expected_value = round(signal.edge * signal.position_size_usd, 2)

                signals.append(signal)
                total_exposure += signal.position_size_usd

        return signals

    def update_bankroll(self, new_bankroll: float):
        """Update bankroll after trades resolve."""
        self.bankroll = new_bankroll
        log.info("bankroll.updated", bankroll=f"${new_bankroll:.2f}")
