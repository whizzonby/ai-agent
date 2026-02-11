"""
Data Enrichment: Fetches external data to give Claude better context.
- Weather: NOAA forecasts and observations
- Sports: injury reports, recent results
- Crypto: on-chain metrics, sentiment signals
"""

import httpx
import structlog
from datetime import datetime, timezone
from scanner import ScannedMarket
from config import config

log = structlog.get_logger()


class DataEnricher:
    """Enriches markets with external data before sending to Claude."""

    def __init__(self):
        self.client = httpx.Client(timeout=15)

    def enrich(self, market: ScannedMarket) -> str:
        """Returns extra context string for the given market category."""
        try:
            if market.category == "weather":
                return self._enrich_weather(market)
            elif market.category == "sports":
                return self._enrich_sports(market)
            elif market.category == "crypto":
                return self._enrich_crypto(market)
            else:
                return ""
        except Exception as e:
            log.warning("enrichment.failed", category=market.category, error=str(e))
            return ""

    # -------------------------------------------------------------------------
    # Weather: NOAA API
    # -------------------------------------------------------------------------
    def _enrich_weather(self, market: ScannedMarket) -> str:
        """Fetch NOAA forecast data relevant to weather prediction markets."""
        # Extract location from question (simplified — production would use NER)
        question = market.question.lower()

        # Example: get national forecast discussion
        try:
            resp = self.client.get(
                "https://api.weather.gov/products/types/AFD",
                headers={"User-Agent": "polymarket-agent/1.0"},
            )
            if resp.status_code == 200:
                products = resp.json().get("@graph", [])[:3]
                discussions = []
                for p in products:
                    detail = self.client.get(
                        p.get("@id", ""),
                        headers={"User-Agent": "polymarket-agent/1.0"},
                    )
                    if detail.status_code == 200:
                        text = detail.json().get("productText", "")[:2000]
                        discussions.append(text)
                return (
                    "[NOAA FORECAST DATA]\n"
                    + "\n---\n".join(discussions)
                    + "\n[END NOAA DATA]"
                )
        except Exception as e:
            log.warning("noaa.fetch_failed", error=str(e))

        return ""

    # -------------------------------------------------------------------------
    # Sports: Public injury / news feeds
    # -------------------------------------------------------------------------
    def _enrich_sports(self, market: ScannedMarket) -> str:
        """Scrape publicly available injury reports and recent results."""
        # Use a free sports API or ESPN public endpoints
        try:
            # ESPN has public endpoints for scores and injuries
            # This is a simplified example — production would parse team names from the question
            resp = self.client.get(
                "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries",
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                injuries = []
                for team in data.get("items", [])[:5]:
                    team_name = team.get("team", {}).get("displayName", "")
                    for player in team.get("injuries", [])[:3]:
                        name = player.get("athlete", {}).get("displayName", "")
                        status = player.get("status", "")
                        injuries.append(f"  {team_name}: {name} - {status}")
                if injuries:
                    return (
                        "[INJURY REPORT]\n"
                        + "\n".join(injuries)
                        + "\n[END INJURY REPORT]"
                    )
        except Exception as e:
            log.warning("sports.fetch_failed", error=str(e))

        return ""

    # -------------------------------------------------------------------------
    # Crypto: On-chain metrics + fear/greed
    # -------------------------------------------------------------------------
    def _enrich_crypto(self, market: ScannedMarket) -> str:
        """Fetch on-chain metrics and sentiment for crypto markets."""
        signals = []

        # Fear & Greed Index (free API)
        try:
            resp = self.client.get("https://api.alternative.me/fng/?limit=1")
            if resp.status_code == 200:
                fng = resp.json()["data"][0]
                signals.append(
                    f"Fear & Greed Index: {fng['value']} ({fng['value_classification']})"
                )
        except Exception:
            pass

        # Bitcoin price from CoinGecko (free, no key needed)
        try:
            resp = self.client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin,ethereum", "vs_currencies": "usd",
                        "include_24hr_change": "true"},
            )
            if resp.status_code == 200:
                data = resp.json()
                for coin in ["bitcoin", "ethereum"]:
                    if coin in data:
                        price = data[coin]["usd"]
                        change = data[coin].get("usd_24h_change", 0)
                        signals.append(f"{coin.upper()}: ${price:,.0f} ({change:+.1f}% 24h)")
        except Exception:
            pass

        # Blockchain.com mempool (Bitcoin)
        try:
            resp = self.client.get("https://api.blockchain.info/mempool?timespan=1hours&format=json")
            if resp.status_code == 200:
                mempool = resp.json()
                if mempool.get("values"):
                    latest = mempool["values"][-1]
                    signals.append(f"BTC mempool transactions: {latest.get('y', 'N/A')}")
        except Exception:
            pass

        if signals:
            return "[CRYPTO SIGNALS]\n" + "\n".join(signals) + "\n[END CRYPTO SIGNALS]"
        return ""
