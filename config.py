"""Centralized configuration loaded from .env"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


@dataclass
class Config:
    # Polymarket
    private_key: str = field(default_factory=lambda: _env("PRIVATE_KEY"))
    funder_address: str = field(default_factory=lambda: _env("FUNDER_ADDRESS"))
    clob_url: str = field(default_factory=lambda: _env("POLYMARKET_CLOB_URL", "https://clob.polymarket.com"))
    gamma_url: str = field(default_factory=lambda: _env("POLYMARKET_GAMMA_URL", "https://gamma-api.polymarket.com"))
    chain_id: int = field(default_factory=lambda: int(_env("CHAIN_ID", "137")))
    signature_type: int = field(default_factory=lambda: int(_env("SIGNATURE_TYPE", "0")))

    # Claude
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    claude_model: str = field(default_factory=lambda: _env("CLAUDE_MODEL", "claude-sonnet-4-20250514"))

    # Data sources
    noaa_api_key: str = field(default_factory=lambda: _env("NOAA_API_KEY"))

    # Trading
    scan_interval: int = field(default_factory=lambda: int(_env("SCAN_INTERVAL_SECONDS", "600")))
    min_edge: float = field(default_factory=lambda: float(_env("MIN_EDGE_PERCENT", "8.0")))
    max_position_pct: float = field(default_factory=lambda: float(_env("MAX_POSITION_PERCENT", "6.0")))
    starting_bankroll: float = field(default_factory=lambda: float(_env("STARTING_BANKROLL", "50.0")))
    min_liquidity: float = field(default_factory=lambda: float(_env("MIN_LIQUIDITY_USD", "500")))
    max_markets: int = field(default_factory=lambda: int(_env("MAX_MARKETS_PER_SCAN", "1000")))

    # Self-funding
    api_cost_buffer: float = field(default_factory=lambda: float(_env("API_COST_BUFFER_USD", "5.0")))
    death_threshold: float = field(default_factory=lambda: float(_env("DEATH_THRESHOLD_USD", "0.50")))

    def validate(self):
        if not self.private_key:
            raise ValueError("PRIVATE_KEY is required in .env")
        if not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required in .env")


config = Config()
