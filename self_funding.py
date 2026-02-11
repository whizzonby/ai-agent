"""
Self-Funding & Death Check

Tracks the agent's P&L, estimates API costs, and determines
whether the agent can afford to keep running.

If balance hits $0 → the agent dies.
"""

import json
import time
import structlog
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone
from config import config

log = structlog.get_logger()

STATE_FILE = Path("agent_state.json")


@dataclass
class AgentState:
    """Persistent state tracking for the agent."""
    starting_bankroll: float = config.starting_bankroll
    current_bankroll: float = config.starting_bankroll
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    total_api_cost: float = 0.0
    total_fees: float = 0.0
    positions_open: int = 0
    started_at: str = ""
    last_cycle_at: str = ""
    cycles_completed: int = 0
    is_alive: bool = True
    trade_history: list = field(default_factory=list)

    def save(self):
        """Persist state to disk."""
        STATE_FILE.write_text(json.dumps(self.__dict__, indent=2, default=str))

    @classmethod
    def load(cls) -> "AgentState":
        """Load state from disk, or create new."""
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                state = cls()
                for k, v in data.items():
                    if hasattr(state, k):
                        setattr(state, k, v)
                return state
            except Exception as e:
                log.warning("state.load_error", error=str(e))
        state = cls()
        state.started_at = datetime.now(timezone.utc).isoformat()
        return state


class SelfFundingManager:
    """Manages the agent's finances and survival."""

    def __init__(self, state: AgentState):
        self.state = state

    def record_trade(self, pnl: float, api_cost_increment: float):
        """Record a completed trade."""
        self.state.total_trades += 1
        self.state.total_pnl += pnl
        self.state.total_api_cost += api_cost_increment

        if pnl > 0:
            self.state.winning_trades += 1
        else:
            self.state.losing_trades += 1

        self.state.current_bankroll += pnl - api_cost_increment
        self.state.trade_history.append({
            "pnl": pnl,
            "api_cost": api_cost_increment,
            "bankroll_after": self.state.current_bankroll,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # Keep only last 1000 trades in history
        if len(self.state.trade_history) > 1000:
            self.state.trade_history = self.state.trade_history[-1000:]

        self.state.save()

    def record_cycle_cost(self, api_cost: float):
        """Record API cost for a scan cycle (even without trades)."""
        self.state.total_api_cost += api_cost
        self.state.current_bankroll -= api_cost
        self.state.cycles_completed += 1
        self.state.last_cycle_at = datetime.now(timezone.utc).isoformat()
        self.state.save()

    def can_afford_cycle(self) -> bool:
        """Check if agent can afford another scan cycle.

        A cycle costs roughly:
        - ~50-80 markets sent to Claude for analysis
        - ~$0.01-0.05 per Claude call (Sonnet)
        - Total ~$0.50-5.00 per cycle
        """
        estimated_cycle_cost = 2.0  # conservative estimate
        return self.state.current_bankroll > (
            estimated_cycle_cost + config.death_threshold
        )

    def sync_balance_from_chain(self):
        """Periodically sync bankroll with actual on-chain balance.

        This corrects for:
        - Trades that resolved (profit/loss)
        - Manual deposits or withdrawals
        - Any tracking drift
        """
        try:
            from balance import get_usdc_balance
            on_chain = get_usdc_balance()
            if on_chain > 0:
                drift = abs(on_chain - self.state.current_bankroll)
                if drift > 0.50:  # only update if meaningful difference
                    log.info(
                        "funding.balance_sync",
                        tracked=f"${self.state.current_bankroll:.2f}",
                        on_chain=f"${on_chain:.2f}",
                        drift=f"${drift:.2f}",
                    )
                    self.state.current_bankroll = on_chain
                    self.state.save()
        except Exception as e:
            log.warning("funding.sync_failed", error=str(e))

    def get_net_balance(self) -> float:
        """Net balance = bankroll - outstanding API costs."""
        return self.state.current_bankroll

    def summary(self) -> str:
        """Human-readable status summary."""
        win_rate = (
            self.state.winning_trades / self.state.total_trades * 100
            if self.state.total_trades > 0
            else 0
        )
        roi = (
            (self.state.current_bankroll - self.state.starting_bankroll)
            / self.state.starting_bankroll
            * 100
        )
        return (
            f"📊 Agent Status\n"
            f"  Bankroll: ${self.state.current_bankroll:.2f} "
            f"(started: ${self.state.starting_bankroll:.2f})\n"
            f"  P&L: ${self.state.total_pnl:+.2f} | ROI: {roi:+.1f}%\n"
            f"  Trades: {self.state.total_trades} "
            f"(W:{self.state.winning_trades} L:{self.state.losing_trades} "
            f"WR:{win_rate:.0f}%)\n"
            f"  API Costs: ${self.state.total_api_cost:.2f}\n"
            f"  Cycles: {self.state.cycles_completed}\n"
            f"  Alive since: {self.state.started_at}\n"
        )


class DeathCheck:
    """Determines if the agent should shut down."""

    def __init__(self, state: AgentState):
        self.state = state

    def is_dead(self) -> bool:
        """Returns True if the agent should terminate."""
        if self.state.current_bankroll <= config.death_threshold:
            log.critical(
                "death_check.DEAD",
                bankroll=f"${self.state.current_bankroll:.2f}",
                threshold=f"${config.death_threshold:.2f}",
            )
            self.state.is_alive = False
            self.state.save()
            return True
        return False

    def health_report(self) -> dict:
        """Return health metrics for monitoring."""
        return {
            "alive": not self.is_dead(),
            "bankroll": self.state.current_bankroll,
            "total_pnl": self.state.total_pnl,
            "api_cost": self.state.total_api_cost,
            "net_profit": self.state.total_pnl - self.state.total_api_cost,
            "trades": self.state.total_trades,
            "cycles": self.state.cycles_completed,
            "runway_cycles": max(
                0, int((self.state.current_bankroll - config.death_threshold) / 2.0)
            ),
        }
