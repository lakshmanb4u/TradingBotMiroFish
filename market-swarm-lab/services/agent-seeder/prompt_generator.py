"""
Simulation Prompt Generator.

Builds a structured prompt and MiroFish seed packet from the agent roster and seed data.
"""
from __future__ import annotations

from typing import Any


class SimulationPromptGenerator:
    def generate(
        self,
        ticker: str,
        seed: dict[str, Any],
        agent_roster: dict[str, Any],
        horizon_days: int = 5,
    ) -> dict[str, Any]:
        archetypes = agent_roster.get("archetypes", {})
        roster = agent_roster.get("agent_roster", [])

        # ── context sections
        fundamentals = seed.get("fundamental_summary", "No fundamental data available.")
        sentiment_ctx = seed.get("retail_sentiment_summary", seed.get("sentiment_summary", "No sentiment data available."))
        news_ctx = seed.get("news_summary", "No news available.")
        probabilities = seed.get("prediction_market_summary", "No prediction market data.")
        forecast_ctx = seed.get("timesfm_summary", "No forecast available.")

        context_sections = {
            "fundamentals": fundamentals,
            "sentiment": sentiment_ctx,
            "news": news_ctx,
            "probabilities": probabilities,
            "forecast": forecast_ctx,
        }

        # ── simulation prompt
        retail_count = archetypes.get("retail", {}).get("count", 3)
        inst_count = archetypes.get("institutional", {}).get("count", 3)
        mom_count = archetypes.get("momentum", {}).get("count", 2)
        cont_count = archetypes.get("contrarian", {}).get("count", 2)

        simulation_prompt = (
            f"This is a {horizon_days}-day market simulation for {ticker.upper()} with {len(roster)} agents. "
            f"The {retail_count} retail agents have access to Reddit sentiment and recent news headlines, "
            f"forming their views from community discussion and media coverage. "
            f"The {inst_count} institutional agents rely on fundamental SEC filings and the TimesFM quantitative forecast, "
            f"approaching the market with structured analysis and higher conviction. "
            f"The {mom_count} momentum agents track price action, RSI, and VWAP alongside the directional forecast, "
            f"amplifying whatever trend emerges among the broader group. "
            f"The {cont_count} contrarian agents monitor the disagreement index and prediction market probabilities, "
            f"positioning against the crowd when conviction becomes extreme. "
            f"Given the current fundamental picture ({fundamentals[:120]}), "
            f"the retail sentiment ({sentiment_ctx[:100]}), "
            f"and the forecast ({forecast_ctx[:120]}), "
            f"what is the most likely price direction for {ticker.upper()} over the next {horizon_days} days, "
            f"and which agent archetype will drive the outcome?"
        )

        # ── agent summary
        counts_by_arch: dict[str, int] = {}
        biases_by_arch: dict[str, list[str]] = {}
        for a in roster:
            arch = a["archetype"]
            counts_by_arch[arch] = counts_by_arch.get(arch, 0) + 1
            biases_by_arch.setdefault(arch, []).append(a["initial_bias"])

        summary_parts = []
        for arch, count in counts_by_arch.items():
            biases = biases_by_arch.get(arch, [])
            bull = biases.count("bullish")
            bear = biases.count("bearish")
            neut = biases.count("neutral")
            summary_parts.append(
                f"{count} {arch} agents ({bull} bullish, {bear} bearish, {neut} neutral)"
            )
        agent_summary = f"Roster: {'; '.join(summary_parts)}."

        # ── MiroFish seed packet
        documents = [
            {
                "source": "fundamentals",
                "content": fundamentals,
            },
            {
                "source": "sentiment",
                "content": sentiment_ctx,
            },
            {
                "source": "news",
                "content": news_ctx,
            },
            {
                "source": "prediction_markets",
                "content": probabilities,
            },
            {
                "source": "forecast",
                "content": forecast_ctx,
            },
        ]

        forecast_summary = {
            "direction": _extract_direction(forecast_ctx),
            "summary": forecast_ctx,
            "horizon_days": horizon_days,
            "ticker": ticker.upper(),
        }

        personas_config = [
            {
                "name": f"{ticker.upper()} Retail Trader",
                "archetype": "retail",
                "count": retail_count,
                "stance": _majority_bias(biases_by_arch.get("retail", [])),
                "information_sources": ["reddit", "news"],
                "weight": 1.0,
            },
            {
                "name": f"{ticker.upper()} Institutional Analyst",
                "archetype": "institutional",
                "count": inst_count,
                "stance": _majority_bias(biases_by_arch.get("institutional", [])),
                "information_sources": ["sec", "timesfm"],
                "weight": 1.0,
            },
            {
                "name": f"{ticker.upper()} Momentum Trader",
                "archetype": "momentum",
                "count": mom_count,
                "stance": _majority_bias(biases_by_arch.get("momentum", [])),
                "information_sources": ["timesfm", "price"],
                "weight": 1.0,
            },
            {
                "name": f"{ticker.upper()} Contrarian",
                "archetype": "contrarian",
                "count": cont_count,
                "stance": _majority_bias(biases_by_arch.get("contrarian", [])),
                "information_sources": ["disagreement", "prediction_markets"],
                "weight": 1.0,
            },
        ]

        mirofish_seed_packet = {
            "documents": documents,
            "forecast_summary": forecast_summary,
            "personas_config": personas_config,
            "scenario": simulation_prompt,
        }

        return {
            "ticker": ticker.upper(),
            "horizon_days": horizon_days,
            "simulation_prompt": simulation_prompt,
            "context_sections": context_sections,
            "agent_summary": agent_summary,
            "mirofish_seed_packet": mirofish_seed_packet,
        }


# ── helpers

def _extract_direction(forecast_ctx: str) -> str:
    text = forecast_ctx.lower()
    if "up" in text or "bullish" in text or "rise" in text or "upward" in text:
        return "up"
    if "down" in text or "bearish" in text or "fall" in text or "downward" in text:
        return "down"
    return "sideways"


def _majority_bias(biases: list[str]) -> str:
    if not biases:
        return "neutral"
    bull = biases.count("bullish")
    bear = biases.count("bearish")
    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    return "neutral"
