"""
Polymarket Autonomous Trading Agent
====================================

Main loop: every 10 minutes —
  1. Scan 500-1000 markets
  2. Build fair value estimates with Claude
  3. Find mispricings > 8%
  4. Size positions (Kelly Criterion, max 6% bankroll)
  5. Execute trades
  6. Pay API bill from profits
  7. If balance hits $0 → agent dies

Usage:
    python main.py
"""

import sys
import time
import signal
import structlog
from datetime import datetime, timezone

from config import config
from scanner import MarketScanner
from fair_value import FairValueEngine
from position_sizer import MispricingDetector, KellyPositionSizer
from executor import TradeExecutor
from self_funding import AgentState, SelfFundingManager, DeathCheck

# ── Logging ──────────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)
log = structlog.get_logger()

# ── Graceful shutdown ────────────────────────────────────
shutdown_requested = False

def handle_signal(signum, frame):
    global shutdown_requested
    log.info("shutdown.requested", signal=signum)
    shutdown_requested = True

signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


def run_cycle(
    scanner: MarketScanner,
    fair_value_engine: FairValueEngine,
    mispricing_detector: MispricingDetector,
    position_sizer: KellyPositionSizer,
    executor: TradeExecutor,
    funding: SelfFundingManager,
    death_check: DeathCheck,
) -> bool:
    """Run one complete scan→analyze→trade cycle. Returns False if agent dies."""

    cycle_start = time.time()
    api_cost_before = fair_value_engine.get_api_cost_usd()

    # ── Step 0: Death check ──────────────────────────────
    if death_check.is_dead():
        log.critical("agent.DEAD", msg="Balance below threshold. Agent terminated.")
        print("\n💀 AGENT IS DEAD. Balance: ${:.2f}".format(funding.state.current_bankroll))
        return False

    # ── Step 1: Scan markets ─────────────────────────────
    log.info("cycle.step1_scan")
    markets = scanner.scan()
    if not markets:
        log.warning("cycle.no_markets")
        return True

    log.info("cycle.markets_found", count=len(markets))

    # ── Step 2: Pre-filter before sending to Claude ──────
    # Only send the most promising markets to Claude to save API costs.
    # Heuristic: prioritize by volume, extreme prices, and category diversity.
    candidates = _prefilter_markets(markets, max_candidates=80)
    log.info("cycle.candidates_for_analysis", count=len(candidates))

    # ── Step 3: Get fair value estimates from Claude ─────
    log.info("cycle.step3_fair_value")
    estimates = fair_value_engine.estimate_batch(candidates)
    log.info("cycle.estimates_received", count=len(estimates))

    # ── Step 4: Find mispricings ─────────────────────────
    log.info("cycle.step4_mispricing")
    mispriced = mispricing_detector.find_signals(estimates)
    log.info("cycle.mispricings_found", count=len(mispriced))

    # ── Step 5: Size positions ───────────────────────────
    log.info("cycle.step5_position_sizing")
    position_sizer.update_bankroll(funding.state.current_bankroll)
    signals = position_sizer.size_batch(mispriced)
    log.info("cycle.signals_generated", count=len(signals))

    # ── Step 6: Execute trades ───────────────────────────
    if signals:
        log.info("cycle.step6_execute", trades=len(signals))
        results = executor.execute_batch(signals)

        for result in results:
            if result.success:
                log.info(
                    "cycle.trade_executed",
                    question=result.signal.estimate.market.question[:50],
                    side=result.signal.side,
                    size=f"${result.signal.position_size_usd:.2f}",
                    order_id=result.order_id,
                )
            else:
                log.warning(
                    "cycle.trade_failed",
                    question=result.signal.estimate.market.question[:50],
                    error=result.error[:100] if result.error else "unknown",
                )
    else:
        log.info("cycle.no_trades", msg="No actionable signals this cycle")

    # ── Step 7: Account for API costs ────────────────────
    api_cost_after = fair_value_engine.get_api_cost_usd()
    cycle_api_cost = api_cost_after - api_cost_before
    funding.record_cycle_cost(cycle_api_cost)

    # ── Cycle summary ────────────────────────────────────
    elapsed = time.time() - cycle_start

    # Sync with on-chain balance every 6 cycles (~1 hour)
    if funding.state.cycles_completed % 6 == 0:
        funding.sync_balance_from_chain()

    log.info(
        "cycle.complete",
        elapsed_sec=f"{elapsed:.1f}",
        markets_scanned=len(markets),
        analyzed=len(candidates),
        mispricings=len(mispriced),
        trades=len(signals),
        api_cost=f"${cycle_api_cost:.4f}",
        bankroll=f"${funding.state.current_bankroll:.2f}",
    )
    print(funding.summary())

    return True


def _prefilter_markets(markets, max_candidates=80):
    """Smart pre-filter to reduce Claude API calls.

    Strategy:
    - Always include top markets by volume (liquid = less noise)
    - Include markets with extreme prices (<0.15 or >0.85) — likely mispriced
    - Include recent markets (new = less efficient)
    - Diversify across categories
    """
    scored = []
    for m in markets:
        score = 0
        # High volume = liquid, reliable
        score += min(m.volume_24h / 10000, 5)
        # Extreme prices are more likely mispriced
        if m.yes_price < 0.15 or m.yes_price > 0.85:
            score += 3
        if m.yes_price < 0.05 or m.yes_price > 0.95:
            score += 5
        # Skip very extreme prices (likely already resolved or dust)
        if m.yes_price < 0.02 or m.yes_price > 0.98:
            continue
        # Weather markets: we have NOAA edge
        if m.category == "weather":
            score += 4
        # Sports: injury report edge
        if m.category == "sports":
            score += 3
        # Crypto: on-chain edge
        if m.category == "crypto":
            score += 2
        scored.append((score, m))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored[:max_candidates]]


def main():
    """Main entry point."""
    config.validate()

    log.info("agent.starting", bankroll=f"${config.starting_bankroll:.2f}")
    print("=" * 60)
    print("🤖 POLYMARKET AUTONOMOUS TRADING AGENT")
    print(f"   Starting bankroll: ${config.starting_bankroll:.2f}")
    print(f"   Min edge: {config.min_edge}%")
    print(f"   Max position: {config.max_position_pct}% of bankroll")
    print(f"   Scan interval: {config.scan_interval}s")
    print(f"   Death threshold: ${config.death_threshold}")
    print("=" * 60)
    print("   'Pay for yourself or you die.'")
    print("=" * 60)

    # ── Pre-flight checks ────────────────────────────────
    print("\n🔍 Running pre-flight checks...")

    # 1. Check on-chain USDC balance
    try:
        from balance import get_usdc_balance, check_allowances
        usdc_balance = get_usdc_balance()
        print(f"   💰 USDC balance: ${usdc_balance:.2f}")
        if usdc_balance < config.starting_bankroll:
            print(f"   ⚠️  Balance (${usdc_balance:.2f}) < starting bankroll (${config.starting_bankroll:.2f})")
            print(f"      Adjusting starting bankroll to ${usdc_balance:.2f}")
            config.starting_bankroll = usdc_balance
    except Exception as e:
        print(f"   ⚠️  Could not check balance: {e}")
        print(f"      Proceeding with configured bankroll: ${config.starting_bankroll:.2f}")

    # 2. Check token allowances
    try:
        allowances = check_allowances()
        missing = [k for k, v in allowances.items() if not v]
        if missing:
            print(f"   ❌ Missing allowances: {', '.join(missing)}")
            print(f"      Run: python setup_allowances.py")
            sys.exit(1)
        else:
            print("   ✅ Token allowances OK")
    except Exception as e:
        print(f"   ⚠️  Could not check allowances: {e}")

    # ── Initialize components ────────────────────────────
    state = AgentState.load()
    if not state.started_at:
        state.started_at = datetime.now(timezone.utc).isoformat()
        state.current_bankroll = config.starting_bankroll
        state.starting_bankroll = config.starting_bankroll
        state.save()

    scanner = MarketScanner()
    fair_value_engine = FairValueEngine()
    mispricing_detector = MispricingDetector()
    position_sizer = KellyPositionSizer(bankroll=state.current_bankroll)

    # 3. Check CLOB API connectivity
    try:
        executor = TradeExecutor()
        if executor.check_connectivity():
            print("   ✅ CLOB API connected")
        else:
            print("   ❌ CLOB API not reachable")
            sys.exit(1)
    except Exception as e:
        print(f"   ❌ Executor init failed: {e}")
        sys.exit(1)

    funding = SelfFundingManager(state)
    death_check = DeathCheck(state)

    print(f"\n🚀 Agent starting with ${state.current_bankroll:.2f} bankroll\n")

    # ── Main loop ────────────────────────────────────────
    while not shutdown_requested:
        try:
            # Check if we can afford to run
            if not funding.can_afford_cycle():
                log.warning("agent.low_funds", bankroll=f"${state.current_bankroll:.2f}")

            alive = run_cycle(
                scanner=scanner,
                fair_value_engine=fair_value_engine,
                mispricing_detector=mispricing_detector,
                position_sizer=position_sizer,
                executor=executor,
                funding=funding,
                death_check=death_check,
            )

            if not alive:
                print("\n💀 AGENT DIED. Final state:")
                print(funding.summary())
                health = death_check.health_report()
                print(f"   Net profit: ${health['net_profit']:.2f}")
                print(f"   Total cycles: {health['cycles']}")
                sys.exit(0)

            # Wait for next cycle
            log.info("agent.sleeping", seconds=config.scan_interval)
            for _ in range(config.scan_interval):
                if shutdown_requested:
                    break
                time.sleep(1)

        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error("agent.cycle_error", error=str(e))
            # Don't die on transient errors, just wait and retry
            time.sleep(60)

    # ── Graceful shutdown ────────────────────────────────
    log.info("agent.shutdown")
    state.save()
    print("\n🛑 Agent shut down gracefully.")
    print(funding.summary())


if __name__ == "__main__":
    main()
