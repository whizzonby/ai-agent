"""
Fair Value Engine: Sends market questions + enrichment data to Claude
and gets back probability estimates.

This is the "brain" of the agent — Claude reasons about whether markets
are mispriced based on the question, description, and external data.
"""

import json
import anthropic
import structlog
from dataclasses import dataclass
from scanner import ScannedMarket
from data_enrichment import DataEnricher
from config import config

log = structlog.get_logger()


@dataclass
class FairValueEstimate:
    market: ScannedMarket
    fair_yes_prob: float  # Claude's estimated true probability of YES
    confidence: float     # 0-1, how confident Claude is in the estimate
    reasoning: str        # Claude's explanation
    edge: float           # fair_yes_prob - market_yes_price (signed)
    abs_edge: float       # absolute edge
    recommended_side: str # "YES" or "NO"
    input_tokens: int     # for cost tracking
    output_tokens: int


SYSTEM_PROMPT = """You are an expert prediction market analyst. Your job is to estimate the TRUE probability of event outcomes, independent of what the market currently thinks.

You will be given:
1. A market question and description
2. The current market price (YES probability)
3. External data (weather forecasts, injury reports, crypto metrics, etc.)

Your response must be valid JSON with exactly these fields:
{
    "fair_yes_probability": <float 0.0 to 1.0>,
    "confidence": <float 0.0 to 1.0>,
    "reasoning": "<brief explanation of your estimate>"
}

Guidelines:
- Be calibrated. If you're unsure, your probability should reflect that uncertainty.
- Use the external data when available — it may contain information the market hasn't priced in yet.
- Consider base rates, historical precedents, and logical reasoning.
- A confidence of 0.5 means you're very uncertain about your estimate.
- A confidence of 0.9+ means you have strong evidence.
- Be especially careful with politics — markets are often efficient there.
- For weather: NOAA data is gold. If NOAA says 80% chance of rain, trust it.
- For sports: injury reports can create 10-20% mispricings if the market is slow to react.
- For crypto: on-chain metrics + sentiment can signal short-term moves.

Return ONLY the JSON object, no other text."""


class FairValueEngine:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        self.enricher = DataEnricher()
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def estimate(self, market: ScannedMarket) -> FairValueEstimate | None:
        """Ask Claude to estimate the fair probability for a market."""
        enrichment = self.enricher.enrich(market)

        user_prompt = f"""Market Question: {market.question}

Description: {market.description}

Resolution Source: {market.resolution_source}

Current Market Price (YES): {market.yes_price:.4f} ({market.yes_price*100:.1f}%)
Current Market Price (NO): {market.no_price:.4f} ({market.no_price*100:.1f}%)

24h Volume: ${market.volume_24h:,.0f}
Liquidity: ${market.liquidity:,.0f}
End Date: {market.end_date}
Category: {market.category}

{enrichment}

What is the TRUE probability of YES?"""

        try:
            response = self.client.messages.create(
                model=config.claude_model,
                max_tokens=500,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )

            # Track token usage for cost accounting
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens

            # Parse response
            text = response.content[0].text.strip()
            # Handle potential markdown code fences
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            data = json.loads(text)

            fair_yes = float(data["fair_yes_probability"])
            confidence = float(data["confidence"])
            reasoning = data["reasoning"]

            # Calculate edge
            edge = fair_yes - market.yes_price
            abs_edge = abs(edge)

            # Determine which side to trade
            if edge > 0:
                recommended_side = "YES"  # Market underprices YES
            else:
                recommended_side = "NO"   # Market underprices NO

            estimate = FairValueEstimate(
                market=market,
                fair_yes_prob=fair_yes,
                confidence=confidence,
                reasoning=reasoning,
                edge=edge,
                abs_edge=abs_edge,
                recommended_side=recommended_side,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

            log.info(
                "fair_value.estimate",
                question=market.question[:60],
                market_price=f"{market.yes_price:.2f}",
                fair_value=f"{fair_yes:.2f}",
                edge=f"{edge:+.2f}",
                confidence=f"{confidence:.2f}",
            )
            return estimate

        except json.JSONDecodeError as e:
            log.error("fair_value.json_parse_error", error=str(e), market=market.slug)
            return None
        except anthropic.APIError as e:
            log.error("fair_value.api_error", error=str(e))
            return None
        except Exception as e:
            log.error("fair_value.unexpected_error", error=str(e), market=market.slug)
            return None

    def estimate_batch(self, markets: list[ScannedMarket]) -> list[FairValueEstimate]:
        """Estimate fair values for a batch of markets.

        In production, you'd want to:
        1. Use Claude's batch API for cost savings (50% off)
        2. Parallelize with asyncio
        3. Rate-limit to stay within API limits

        For now, sequential is fine for a prototype.
        """
        estimates = []
        for market in markets:
            est = self.estimate(market)
            if est:
                estimates.append(est)
        return estimates

    def get_api_cost_usd(self) -> float:
        """Estimate total Claude API cost so far.

        Claude Sonnet pricing (check https://docs.anthropic.com for current):
        - Input:  $3.00 / 1M tokens
        - Output: $15.00 / 1M tokens

        These rates may have changed — update if using a different model.
        """
        input_cost = (self.total_input_tokens / 1_000_000) * 3.00
        output_cost = (self.total_output_tokens / 1_000_000) * 15.00
        return input_cost + output_cost
