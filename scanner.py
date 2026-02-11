"""
Scanner: Fetches 500-1000 active markets from Polymarket's Gamma API.
Filters for tradeable markets with sufficient liquidity.

IMPORTANT: The Gamma API returns `outcomePrices` and `clobTokenIds` as
JSON-encoded strings, e.g. '["0.65", "0.35"]'. They must be json.loads()'d.
"""

import json
import time
import httpx
import structlog
from dataclasses import dataclass
from config import config

log = structlog.get_logger()


@dataclass
class ScannedMarket:
    """Lightweight market representation for the pipeline."""
    condition_id: str
    question: str
    slug: str
    outcome_yes_token: str
    outcome_no_token: str
    yes_price: float
    no_price: float
    volume_24h: float
    liquidity: float
    end_date: str
    category: str  # e.g. "sports", "crypto", "weather", "politics"
    description: str
    resolution_source: str
    neg_risk: bool  # whether market supports negative risk (multi-outcome events)


class MarketScanner:
    def __init__(self):
        self.gamma_url = config.gamma_url
        self.client = httpx.Client(timeout=30)

    def scan(self) -> list[ScannedMarket]:
        """Fetch all active, liquid markets from Gamma API."""
        markets = []
        offset = 0
        limit = 100  # Gamma API max page size

        while len(markets) < config.max_markets:
            try:
                resp = self.client.get(
                    f"{self.gamma_url}/markets",
                    params={
                        "limit": limit,
                        "offset": offset,
                        "active": "true",
                        "closed": "false",
                        "order": "volume24hr",
                        "ascending": "false",
                    },
                )
                resp.raise_for_status()
                batch = resp.json()

                if not batch:
                    break

                for m in batch:
                    parsed = self._parse_market(m)
                    if parsed and parsed.liquidity >= config.min_liquidity:
                        markets.append(parsed)

                offset += limit
                log.info("scanner.batch", fetched=len(batch), total=len(markets))

                # Respect rate limits — small delay between pages
                time.sleep(0.25)

            except httpx.HTTPError as e:
                log.error("scanner.http_error", error=str(e), offset=offset)
                break

        log.info("scanner.complete", total_markets=len(markets))
        return markets

    def _parse_market(self, raw: dict) -> ScannedMarket | None:
        """Parse raw Gamma API response into ScannedMarket.

        Key gotcha: outcomePrices and clobTokenIds come as JSON-encoded strings
        from the API, e.g. '["0.65", "0.35"]', NOT as native arrays.
        """
        try:
            # --- Parse clobTokenIds (JSON-encoded string) ---
            raw_tokens = raw.get("clobTokenIds", "[]")
            if isinstance(raw_tokens, str):
                tokens = json.loads(raw_tokens)
            else:
                tokens = raw_tokens
            if not tokens or len(tokens) < 2:
                return None

            # --- Parse outcomePrices (JSON-encoded string) ---
            raw_prices = raw.get("outcomePrices", '["0.5", "0.5"]')
            if isinstance(raw_prices, str):
                prices = json.loads(raw_prices)
            else:
                prices = raw_prices
            if not prices or len(prices) < 2:
                return None

            yes_price = float(prices[0])
            no_price = float(prices[1])

            # Skip markets with invalid prices
            if yes_price <= 0 or no_price <= 0:
                return None
            if yes_price >= 1.0 and no_price >= 1.0:
                return None

            question = raw.get("question", "")
            category = self._infer_category(raw)

            # Volume and liquidity can be strings or numbers depending on the field
            volume_24h = float(raw.get("volume24hr", 0) or 0)
            liquidity = float(raw.get("liquidityNum", 0) or raw.get("liquidity", 0) or 0)

            return ScannedMarket(
                condition_id=raw.get("conditionId", ""),
                question=question,
                slug=raw.get("slug", ""),
                outcome_yes_token=tokens[0],
                outcome_no_token=tokens[1],
                yes_price=yes_price,
                no_price=no_price,
                volume_24h=volume_24h,
                liquidity=liquidity,
                end_date=raw.get("endDate", ""),
                category=category,
                description=raw.get("description", "")[:1000],  # truncate long descriptions
                resolution_source=raw.get("resolutionSource", ""),
                neg_risk=bool(raw.get("negRisk", False)),
            )
        except (ValueError, IndexError, KeyError, json.JSONDecodeError) as e:
            log.warning("scanner.parse_error", error=str(e), slug=raw.get("slug"))
            return None

    def _infer_category(self, raw: dict) -> str:
        """Infer market category from tags and question text."""
        question = raw.get("question", "").lower()
        tags = [t.get("label", "").lower() for t in raw.get("tags", [])]
        all_text = question + " " + " ".join(tags)

        if any(w in all_text for w in ["weather", "temperature", "rain", "hurricane", "noaa", "forecast"]):
            return "weather"
        if any(w in all_text for w in ["nfl", "nba", "mlb", "nhl", "soccer", "sport", "game", "match", "ufc", "boxing"]):
            return "sports"
        if any(w in all_text for w in ["bitcoin", "ethereum", "crypto", "btc", "eth", "token", "defi", "solana"]):
            return "crypto"
        if any(w in all_text for w in ["election", "president", "congress", "senate", "vote", "poll", "governor"]):
            return "politics"
        return "other"
