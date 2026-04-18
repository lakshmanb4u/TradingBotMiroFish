"""
Agent Seeder Service.

Builds a 100-agent roster with four archetypes and runs a multi-day
sentiment simulation over them.
"""
from __future__ import annotations

import random
from typing import Any


class AgentSeederService:
    # ──────────────────────────────────────────── public

    def seed_agents(
        self,
        seed: dict[str, Any],
        forecast: dict[str, Any],
        normalized_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = normalized_bundle.get("snapshot", {})
        reddit_sentiment = float(seed.get("weighted_sentiment", snapshot.get("reddit_sentiment", 0.0)))
        forecast_direction = forecast.get("direction", "sideways")
        forecast_confidence = float(forecast.get("confidence", 0.5))
        forecast_close_5d = forecast.get("forecast_close_5d", snapshot.get("latest_close", 0.0))
        delta_5d = forecast.get("delta_5d", 0.0)
        horizon_days = 5

        # Majority bias for contrarians
        majority_bias = "bullish" if reddit_sentiment > 0 else "bearish"

        roster: list[dict] = []

        # ── retail (40)
        retail_prompt = self._retail_prompt(seed, snapshot)
        for i in range(40):
            bias = "bullish" if reddit_sentiment > 0 else ("bearish" if reddit_sentiment < 0 else "neutral")
            strength = round(min(1.0, abs(reddit_sentiment) + 0.1 * (i % 5)), 3)
            roster.append({
                "id": f"retail_{i+1:03d}",
                "archetype": "retail",
                "prompt_section": retail_prompt,
                "initial_bias": bias,
                "bias_strength": strength,
                "weight": 1.0,
            })

        # ── institutional (30)
        inst_prompt = self._institutional_prompt(seed, forecast_direction, forecast_close_5d, delta_5d, horizon_days, forecast_confidence)
        for i in range(30):
            bias = "bullish" if forecast_direction == "up" else ("bearish" if forecast_direction == "down" else "neutral")
            strength = round(min(1.0, forecast_confidence + 0.05 * (i % 4)), 3)
            roster.append({
                "id": f"institutional_{i+1:03d}",
                "archetype": "institutional",
                "prompt_section": inst_prompt,
                "initial_bias": bias,
                "bias_strength": strength,
                "weight": 1.0,
            })

        # ── momentum (20)
        mom_prompt = self._momentum_prompt(seed, snapshot, forecast_direction)
        for i in range(20):
            bias = "bullish" if forecast_direction == "up" else ("bearish" if forecast_direction == "down" else "neutral")
            # Amplified: higher base strength
            strength = round(min(1.0, forecast_confidence * 1.2 + 0.05 * (i % 3)), 3)
            roster.append({
                "id": f"momentum_{i+1:03d}",
                "archetype": "momentum",
                "prompt_section": mom_prompt,
                "initial_bias": bias,
                "bias_strength": strength,
                "weight": 1.0,
            })

        # ── contrarian (10)
        cont_prompt = self._contrarian_prompt(seed)
        for i in range(10):
            bias = "bearish" if majority_bias == "bullish" else "bullish"
            strength = round(min(1.0, 0.4 + 0.05 * (i % 4)), 3)
            roster.append({
                "id": f"contrarian_{i+1:03d}",
                "archetype": "contrarian",
                "prompt_section": cont_prompt,
                "initial_bias": bias,
                "bias_strength": strength,
                "weight": 1.0,
            })

        archetypes = {
            "retail": {"count": 40, "section": retail_prompt},
            "institutional": {"count": 30, "section": inst_prompt},
            "momentum": {"count": 20, "section": mom_prompt},
            "contrarian": {"count": 10, "section": cont_prompt},
        }

        information_asymmetry = {
            "retail_sees": ["reddit", "news"],
            "institutional_sees": ["sec", "timesfm"],
            "momentum_sees": ["timesfm", "price"],
            "contrarian_sees": ["disagreement", "prediction_markets"],
        }

        return {
            "agent_roster": roster,
            "archetypes": archetypes,
            "information_asymmetry": information_asymmetry,
        }

    def run_simulation(
        self,
        agent_roster: dict[str, Any],
        horizon_days: int = 5,
    ) -> dict[str, Any]:
        agents = agent_roster.get("agent_roster", [])
        if not agents:
            return {
                "sentiment_per_day": [],
                "final_direction": "rangebound",
                "buy_sell_ratio": 1.0,
                "top_reasons": ["No agents in roster."],
            }

        # Convert bias to numeric: bullish=+1, bearish=-1, neutral=0
        def bias_value(b: str) -> float:
            return 1.0 if b == "bullish" else (-1.0 if b == "bearish" else 0.0)

        # Initialize agent state
        state = [
            {
                "id": a["id"],
                "archetype": a["archetype"],
                "bias_val": bias_value(a["initial_bias"]),
                "bias_strength": float(a["bias_strength"]),
                "weight": float(a["weight"]),
                "initial_bias": a["initial_bias"],
            }
            for a in agents
        ]

        sentiment_per_day: list[dict] = []
        prev_sentiment = 0.0

        for day in range(1, horizon_days + 1):
            # ── compute aggregate sentiment
            total_w = sum(abs(s["bias_val"]) * s["bias_strength"] * s["weight"] for s in state)
            if total_w == 0:
                agg = 0.0
            else:
                agg = sum(s["bias_val"] * s["bias_strength"] * s["weight"] for s in state) / max(total_w, 1e-9)
            agg = round(max(-1.0, min(1.0, agg)), 4)

            # ── crowd conviction
            crowd_conviction = abs(agg)

            # ── update each agent
            for s in state:
                arch = s["archetype"]
                if arch == "retail":
                    # influenced by prior day momentum (weight 0.4)
                    momentum_influence = prev_sentiment * 0.4
                    new_val = s["bias_val"] + momentum_influence
                    s["bias_val"] = max(-1.0, min(1.0, new_val))
                    s["bias_strength"] = round(min(1.0, s["bias_strength"] + 0.02), 3)

                elif arch == "institutional":
                    # influenced by forecast direction (weight 0.3)
                    forecast_influence = agg * 0.3
                    new_val = s["bias_val"] + forecast_influence
                    s["bias_val"] = max(-1.0, min(1.0, new_val))

                elif arch == "momentum":
                    # amplify prevailing sentiment
                    if agg > 0.2 or agg < -0.2:
                        s["weight"] = round(min(2.0, s["weight"] + 0.2), 3)
                    s["bias_val"] = agg  # follows the crowd

                elif arch == "contrarian":
                    # flip if crowd conviction > 0.7
                    if crowd_conviction > 0.7:
                        s["bias_val"] = -agg
                        s["bias_strength"] = round(min(1.0, s["bias_strength"] + 0.1), 3)

            # ── compute buy/sell pressure
            buy_agents = [s for s in state if s["bias_val"] > 0]
            sell_agents = [s for s in state if s["bias_val"] < 0]
            buy_pressure = round(sum(s["bias_val"] * s["bias_strength"] * s["weight"] for s in buy_agents), 4)
            sell_pressure = round(sum(abs(s["bias_val"]) * s["bias_strength"] * s["weight"] for s in sell_agents), 4)

            # ── dominant archetype (most positive contribution)
            arch_scores: dict[str, float] = {}
            for s in state:
                a = s["archetype"]
                arch_scores[a] = arch_scores.get(a, 0.0) + s["bias_val"] * s["bias_strength"] * s["weight"]
            dominant = max(arch_scores, key=lambda k: abs(arch_scores[k]))

            sentiment_per_day.append({
                "day": day,
                "sentiment_score": agg,
                "buy_pressure": buy_pressure,
                "sell_pressure": sell_pressure,
                "dominant_archetype": dominant,
            })

            prev_sentiment = agg

        # ── final direction
        final_agg = sentiment_per_day[-1]["sentiment_score"] if sentiment_per_day else 0.0
        if final_agg >= 0.1:
            final_direction = "bullish"
        elif final_agg <= -0.1:
            final_direction = "bearish"
        else:
            final_direction = "rangebound"

        last = sentiment_per_day[-1]
        buy_sell_ratio = round(
            (last["buy_pressure"] + 1e-9) / (last["sell_pressure"] + 1e-9), 4
        )

        top_reasons = self._generate_top_reasons(sentiment_per_day, state, final_direction)

        return {
            "sentiment_per_day": sentiment_per_day,
            "final_direction": final_direction,
            "buy_sell_ratio": buy_sell_ratio,
            "top_reasons": top_reasons,
        }

    # ──────────────────────────────────────────── prompt builders

    def _retail_prompt(self, seed: dict, snapshot: dict) -> str:
        ret_summary = seed.get("retail_sentiment_summary", seed.get("sentiment_summary", "No retail sentiment available."))
        news_summary = seed.get("news_summary", "No news available.")
        bullish_pts = seed.get("key_bullish_points", [])
        bearish_pts = seed.get("key_bearish_points", [])
        disagreement = seed.get("disagreement_score", seed.get("disagreement_level", 0.0))

        bull_text = "; ".join(str(p)[:80] for p in bullish_pts[:2]) if bullish_pts else "none"
        bear_text = "; ".join(str(p)[:80] for p in bearish_pts[:2]) if bearish_pts else "none"

        return (
            f"Retail perspective: {ret_summary} "
            f"News context: {news_summary} "
            f"Top bullish signals: {bull_text}. "
            f"Top bearish signals: {bear_text}. "
            f"Community disagreement index: {disagreement:.2f} "
            f"(higher means the crowd is more divided)."
        )

    def _institutional_prompt(
        self, seed: dict, direction: str, close_5d: float,
        delta_5d: float, horizon: int, confidence: float,
    ) -> str:
        fundamental = seed.get("fundamental_summary", "No fundamental data available.")
        timesfm = seed.get("timesfm_summary", "")
        pct = abs(delta_5d / close_5d * 100) if close_5d else 0.0
        direction_label = "upward" if direction == "up" else ("downward" if direction == "down" else "sideways")
        return (
            f"Institutional perspective: {fundamental} "
            f"Model predicts a {pct:.1f}% {direction_label} move over {horizon} days "
            f"with {confidence:.0%} confidence (target: {close_5d}). "
            f"TimesFM insight: {timesfm}"
        )

    def _momentum_prompt(self, seed: dict, snapshot: dict, direction: str) -> str:
        timesfm = seed.get("timesfm_summary", "No forecast available.")
        close = snapshot.get("latest_close", 0.0)
        rsi = snapshot.get("latest_rsi", 50.0)
        vwap = snapshot.get("latest_vwap", 0.0)
        trend = "uptrend" if direction == "up" else ("downtrend" if direction == "down" else "sideways trend")
        return (
            f"Momentum perspective: {timesfm} "
            f"Current price: {close}, RSI: {rsi:.1f}, VWAP: {vwap:.2f}. "
            f"Technical indicators confirm a {trend}. "
            f"Momentum traders are aligned with the prevailing direction."
        )

    def _contrarian_prompt(self, seed: dict) -> str:
        disagreement = seed.get("disagreement_level", seed.get("disagreement_score", 0.0))
        pred_summary = seed.get("prediction_market_summary", "No prediction market data.")
        bearish_pts = seed.get("key_bearish_points", [])
        bear_text = "; ".join(str(p)[:80] for p in bearish_pts[:2]) if bearish_pts else "none"
        return (
            f"Contrarian perspective: The crowd currently shows a disagreement index of {disagreement:.2f}. "
            f"Prediction markets suggest: {pred_summary} "
            f"Key bearish risks the crowd may be overlooking: {bear_text}. "
            f"When crowd conviction is high, consider the opposite trade."
        )

    def _generate_top_reasons(
        self,
        sentiment_per_day: list[dict],
        state: list[dict],
        final_direction: str,
    ) -> list[str]:
        reasons = []
        if sentiment_per_day:
            trend = "increased" if sentiment_per_day[-1]["sentiment_score"] > sentiment_per_day[0]["sentiment_score"] else "decreased"
            reasons.append(
                f"Aggregate sentiment {trend} over {len(sentiment_per_day)} days, "
                f"ending at {sentiment_per_day[-1]['sentiment_score']:.3f}."
            )

        dominant_counts: dict[str, int] = {}
        for d in sentiment_per_day:
            k = d["dominant_archetype"]
            dominant_counts[k] = dominant_counts.get(k, 0) + 1
        if dominant_counts:
            top_arch = max(dominant_counts, key=lambda k: dominant_counts[k])
            reasons.append(f"{top_arch.capitalize()} agents were the dominant force in {dominant_counts[top_arch]} of {len(sentiment_per_day)} days.")

        reasons.append(f"Final simulation direction: {final_direction}.")

        buy_agents = [s for s in state if s["bias_val"] > 0]
        sell_agents = [s for s in state if s["bias_val"] < 0]
        reasons.append(f"{len(buy_agents)} agents ended with bullish bias, {len(sell_agents)} with bearish bias.")

        contrarians = [s for s in state if s["archetype"] == "contrarian"]
        if contrarians:
            flipped = [s for s in contrarians if (final_direction == "bullish" and s["bias_val"] > 0) or (final_direction == "bearish" and s["bias_val"] < 0)]
            reasons.append(f"{len(flipped)} of {len(contrarians)} contrarian agents aligned with the final direction after crowd pressure shifts.")

        return reasons[:5]
