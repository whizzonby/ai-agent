# 🤖 Polymarket Autonomous Trading Agent

> *"Pay for yourself or you die."*

An autonomous AI trading agent that scans Polymarket prediction markets every 10 minutes, estimates fair values using Claude, finds mispricings >8%, sizes positions with Kelly Criterion, executes trades, and pays its own API bills from profits. If balance hits $0, the agent dies.

---

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│                  MAIN LOOP (every 10 min)                  │
│                                                            │
│  ┌───────────┐   ┌───────────┐   ┌────────────┐           │
│  │  Scanner   │──▶│  Claude    │──▶│ Mispricing  │           │
│  │ Gamma API  │   │ Fair Value │   │ Detector    │           │
│  │ 500-1000   │   │ Engine     │   │ edge > 8%   │           │
│  │ markets    │   │            │   │             │           │
│  └───────────┘   └───────────┘   └────────────┘           │
│       │               ▲                  │                 │
│       │          ┌────┘                  ▼                 │
│  ┌────┴──────┐   │           ┌────────────────┐            │
│  │ Data      │   │           │ Kelly Position  │            │
│  │ Enrichment│───┘           │ Sizer (max 6%) │            │
│  │ NOAA/ESPN │               └───────┬────────┘            │
│  │ CoinGecko │                       │                     │
│  └───────────┘                       ▼                     │
│                              ┌────────────────┐            │
│  ┌───────────┐               │   Executor     │            │
│  │ Self-Fund │◀──────────────│  CLOB API      │            │
│  │ Manager   │               │  FOK orders    │            │
│  └─────┬─────┘               └────────────────┘            │
│        │                                                   │
│        ▼                                                   │
│   balance ≤ $0.50 ? ──▶ 💀 AGENT DIES                     │
└────────────────────────────────────────────────────────────┘
```

## File Structure

```
polymarket-agent/
├── main.py              # Main loop — orchestrates everything
├── config.py            # Centralized config from .env
├── scanner.py           # Fetches markets from Gamma API
├── fair_value.py        # Claude estimates true probabilities
├── data_enrichment.py   # NOAA, ESPN injuries, crypto metrics
├── position_sizer.py    # Mispricing detector + Kelly Criterion
├── executor.py          # Places trades via CLOB API
├── self_funding.py      # P&L tracking, death check
├── requirements.txt     # Python dependencies
├── .env.example         # Config template
└── agent_state.json     # Persistent state (auto-created)
```

## Quick Start

### 1. Prerequisites

- Python 3.11+
- A Polygon wallet funded with USDC on Polygon (this is your bankroll)
- An Anthropic API key
- A VPS (any $4.5/month VPS works)

### 2. Setup

```bash
git clone <your-repo>
cd polymarket-agent

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your keys
```

### 3. Configure `.env`

```bash
PRIVATE_KEY=0xYourPolygonPrivateKey
FUNDER_ADDRESS=0xYourAddress
ANTHROPIC_API_KEY=sk-ant-...

# Tweak these as needed
STARTING_BANKROLL=50.0
MIN_EDGE_PERCENT=8.0
MAX_POSITION_PERCENT=6.0
SCAN_INTERVAL_SECONDS=600
```

### 4. Run

```bash
python main.py
```

### 5. Deploy on VPS

```bash
# Install and run as a systemd service
sudo tee /etc/systemd/system/polyagent.service << EOF
[Unit]
Description=Polymarket Trading Agent
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/polymarket-agent
ExecStart=/home/ubuntu/polymarket-agent/venv/bin/python main.py
Restart=on-failure
RestartSec=60
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable polyagent
sudo systemctl start polyagent
sudo journalctl -u polyagent -f  # watch logs
```

---

## How Each Component Works

### Scanner (`scanner.py`)
Hits the Gamma API (`https://gamma-api.polymarket.com/markets`) with pagination, fetching up to 1000 active markets sorted by 24h volume. Parses each into a lightweight `ScannedMarket` dataclass and infers category (weather/sports/crypto/politics/other) from tags and question text. Filters out markets below the liquidity threshold.

### Data Enrichment (`data_enrichment.py`)
Before sending markets to Claude, enriches them with external data the market may not have priced in yet:

| Category | Data Source | Edge |
|----------|------------|------|
| Weather | NOAA Area Forecast Discussions | NOAA publishes before Polymarket updates |
| Sports | ESPN public injury report API | Fresh injury news = mispricing window |
| Crypto | CoinGecko prices, Fear & Greed Index, mempool | On-chain signals + sentiment |

### Fair Value Engine (`fair_value.py`)
Sends each candidate market to Claude with a carefully crafted system prompt. Claude returns a JSON response with `fair_yes_probability`, `confidence`, and `reasoning`. Tracks token usage for cost accounting.

### Mispricing Detector + Position Sizer (`position_sizer.py`)

**Mispricing detection:** Filters for |fair_value - market_price| > 8% and confidence >= 0.4.

**Kelly Criterion:** `f* = (bp - q) / b` where b = net odds, p = fair probability. Uses quarter-Kelly, scales by confidence, caps at 6% bankroll per position, 50% total portfolio exposure.

### Executor (`executor.py`)
Uses `py-clob-client` to check order book depth, verify slippage < 5%, and place Fill-or-Kill market orders.

### Self-Funding (`self_funding.py`)
Tracks API costs and trade P&L in `agent_state.json`. Death check: if bankroll <= $0.50, the agent terminates.

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Quarter-Kelly | ~75% of full Kelly growth with dramatically less variance. Survival > optimal growth at $50 bankroll. |
| 8% min edge | Markets have ~2-5% spreads. 8% buffer protects against Claude miscalibration. |
| FOK orders | Full fill or nothing. No partial fills leaving awkward positions. |
| Pre-filter to ~80 markets | Sending all 1000 to Claude would cost $10-20/cycle. Pre-filtering keeps it at $0.50-2.00. |

## Cost Estimates

| Component | Cost per cycle | Monthly (144 cycles/day) |
|-----------|---------------|-------------------------|
| Claude API (~80 calls) | ~$0.50-2.00 | ~$70-290 |
| VPS | — | $4.50 |
| Polygon gas | negligible | ~$1-2 |

---

## Extending the Agent

- **More data sources:** Political polling aggregators, flight tracking, social media sentiment, Google Trends
- **Better prompts:** Few-shot examples, category-specific prompts, chain-of-thought
- **Position monitoring:** Stop-losses, take-profit exits, time-based closes near expiry
- **Batch API:** Use Anthropic's batch API for 50% cost reduction on non-urgent analysis

## Disclaimer

This is experimental software. Prediction market trading involves real financial risk. The agent can and will lose money. Never risk more than you can afford to lose. This is not financial advice.
