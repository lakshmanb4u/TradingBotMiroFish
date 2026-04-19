"""
Agent Seeder Service.

Builds a 100-agent roster with four archetypes and runs a multi-day
sentiment simulation over them with feedback loops, agent memory,
Reddit influence modelling, and trade signal generation.
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

        # New: pull apify/live reddit features from normalized_bundle when available
        reddit_features: dict[str, Any] = normalized_bundle.get("features", {})

        # Build reddit_context for retail agents from dedicated "reddit" key when present
        reddit_data = normalized_bundle.get("reddit", {})
        reddit_context_for_retail: dict[str, Any] = {}
        if reddit_data.get("provider_mode"):
            _pm = reddit_data["provider_mode"]
            _provider_map = {
                "apify_live": "apify",
                "oauth_live": "oauth",
                "fixture_fallback": "fixture",
            }
            _confidence_map = {"apify_live": 1.0, "oauth_live": 0.6, "fixture_fallback": 0.2}
            _rf = reddit_data.get("features", reddit_features)
            _threads = reddit_data.get("threads", [])
            _top_threads = sorted(_threads, key=lambda t: t.get("score", 0), reverse=True)[:3]
            _bull = float(_rf.get("bullish_ratio", 0.0))
            _bear = float(_rf.get("bearish_ratio", 0.0))
            reddit_context_for_retail = {
                "bullish_ratio": _bull,
                "bearish_ratio": _bear,
                "neutral_ratio": round(max(0.0, 1.0 - _bull - _bear), 4),
                "engagement_velocity": float(_rf.get("engagement_velocity", 0.0)),
                "disagreement_index": float(_rf.get("disagreement_index", 0.0)),
                "unique_author_count": int(_rf.get("unique_author_count", 0)),
                "most_upvoted_arguments": [
                    {
                        "title": t.get("title", ""),
                        "score": t.get("score", 0),
                        "sentiment_label": t.get("sentiment_label", "neutral"),
                    }
                    for t in _top_threads
                ],
                "reddit_confidence": _confidence_map.get(_pm, 0.2),
                "provider": _provider_map.get(_pm, "fixture"),
            }
            # Also prefer nested features for downstream sentiment
            if _rf:
                reddit_features = {**reddit_features, **_rf}

        reddit_sentiment = float(
            reddit_features.get("avg_sentiment",
                seed.get("weighted_sentiment",
                    snapshot.get("reddit_sentiment", 0.0)))
        )
        forecast_direction = forecast.get("direction", "sideways")
        forecast_confidence = float(forecast.get("confidence", 0.5))
        forecast_close_5d = forecast.get("forecast_close_5d", snapshot.get("latest_close", 0.0))
        delta_5d = forecast.get("delta_5d", 0.0)
        horizon_days = 5

        # Majority bias for contrarians
        majority_bias = "bullish" if reddit_sentiment > 0 else "bearish"

        # ── Reddit influence model
        # Weight posts/comments by upvote score, compute per-theme influence
        reddit_posts = normalized_bundle.get("threads", seed.get("reddit_posts", []))
        reddit_comments = normalized_bundle.get("comments", seed.get("reddit_comments", []))
        themes = seed.get("themes", [])

        # Build upvote-weighted influence scores per theme
        theme_influence: dict[str, float] = {}
        total_upvotes = 0.0
        for item in reddit_posts + reddit_comments:
            score = float(item.get("score", item.get("upvotes", 1)))
            total_upvotes += max(score, 1)
            for theme in themes[:5]:
                theme_key = str(theme)
                theme_influence[theme_key] = theme_influence.get(theme_key, 0.0) + max(score, 1)

        # Normalize theme influence scores
        if total_upvotes > 0:
            for k in theme_influence:
                theme_influence[k] = round(theme_influence[k] / total_upvotes, 4)

        # Overall reddit influence multiplier (0.8 – 1.5 range based on upvote density)
        avg_upvote_weight = (total_upvotes / max(len(reddit_posts) + len(reddit_comments), 1)) if reddit_posts or reddit_comments else 1.0
        reddit_influence_score = round(min(1.5, max(0.8, 1.0 + (avg_upvote_weight - 1.0) / 100.0)), 4)

        roster: list[dict] = []

        # ── retail (40) — upvote-weighted bias strength
        retail_prompt = self._retail_prompt(seed, snapshot, reddit_features)
        for i in range(40):
            bias = "bullish" if reddit_sentiment > 0 else ("bearish" if reddit_sentiment < 0 else "neutral")
            base_strength = round(min(1.0, abs(reddit_sentiment) + 0.1 * (i % 5)), 3)
            # Higher upvote weight → stronger initial bias
            strength = round(min(1.0, base_strength * reddit_influence_score), 3)
            agent: dict[str, Any] = {
                "id": f"retail_{i+1:03d}",
                "archetype": "retail",
                "prompt_section": retail_prompt,
                "initial_bias": bias,
                "bias_strength": strength,
                "weight": 1.0,
                "reddit_influence_score": reddit_influence_score,
                "memory": {
                    "decisions": [],
                    "outcomes": [],
                    "confidence": 0.5,
                    "influence": 1.0,
                },
            }
            if reddit_context_for_retail:
                agent["reddit_context"] = reddit_context_for_retail
            roster.append(agent)

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
                "reddit_influence_score": 1.0,
                "memory": {
                    "decisions": [],
                    "outcomes": [],
                    "confidence": 0.5,
                    "influence": 1.0,
                },
            })

        # ── momentum (20)
        mom_prompt = self._momentum_prompt(seed, snapshot, forecast_direction)
        for i in range(20):
            bias = "bullish" if forecast_direction == "up" else ("bearish" if forecast_direction == "down" else "neutral")
            strength = round(min(1.0, forecast_confidence * 1.2 + 0.05 * (i % 3)), 3)
            roster.append({
                "id": f"momentum_{i+1:03d}",
                "archetype": "momentum",
                "prompt_section": mom_prompt,
                "initial_bias": bias,
                "bias_strength": strength,
                "weight": 1.0,
                "reddit_influence_score": 1.0,
                "memory": {
                    "decisions": [],
                    "outcomes": [],
                    "confidence": 0.5,
                    "influence": 1.0,
                },
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
                "reddit_influence_score": 1.0,
                "memory": {
                    "decisions": [],
                    "outcomes": [],
                    "confidence": 0.5,
                    "influence": 1.0,
                },
            })

        archetypes = {
            "retail": {
                "count": 40,
                "section": retail_prompt,
                "reddit_influence_score": reddit_influence_score,
                "theme_influence": theme_influence,
            },
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
            "forecast_direction": forecast_direction,
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
                "price_trajectory": [100.0],
                "forecast_deviation": 0.0,
                "trend_breaks": [],
                "agent_memory_summary": {
                    "avg_confidence": 0.5,
                    "avg_influence": 1.0,
                    "most_influential_agent": "",
                },
            }

        forecast_direction = agent_roster.get("forecast_direction", "sideways")
        forecast_is_up = forecast_direction == "up"

        def bias_value(b: str) -> float:
            return 1.0 if b == "bullish" else (-1.0 if b == "bearish" else 0.0)

        # Initialize agent state (copy memory from roster)
        state = []
        for a in agents:
            mem = a.get("memory", {})
            state.append({
                "id": a["id"],
                "archetype": a["archetype"],
                "bias_val": bias_value(a["initial_bias"]),
                "bias_strength": float(a["bias_strength"]),
                "weight": float(a["weight"]),
                "initial_bias": a["initial_bias"],
                "reddit_influence_score": float(a.get("reddit_influence_score", 1.0)),
                "memory": {
                    "decisions": list(mem.get("decisions", [])),
                    "outcomes": list(mem.get("outcomes", [])),
                    "confidence": float(mem.get("confidence", 0.5)),
                    "influence": float(mem.get("influence", 1.0)),
                },
            })

        sentiment_per_day: list[dict] = []
        prev_sentiment = 0.0
        prev_direction: str | None = None        # "up" | "down" | "flat"
        consecutive_break_days = 0
        price_index = 100.0
        price_trajectory: list[float] = [price_index]
        trend_breaks: list[int] = []
        initial_forecast_sentiment: float | None = None

        for day in range(1, horizon_days + 1):
            # ── 1. Weighted aggregate sentiment (bias_strength * weight * influence)
            weighted_num = sum(
                s["bias_val"] * s["bias_strength"] * s["weight"] * s["memory"]["influence"]
                for s in state
            )
            weighted_den = sum(
                abs(s["bias_val"]) * s["bias_strength"] * s["weight"] * s["memory"]["influence"]
                for s in state
            )
            if weighted_den < 1e-9:
                agg = 0.0
            else:
                agg = weighted_num / weighted_den
            agg = round(max(-1.0, min(1.0, agg)), 4)

            # ── 2. Disagreement index (std-dev proxy)
            vals = [s["bias_val"] * s["bias_strength"] for s in state]
            mean_val = sum(vals) / len(vals)
            variance = sum((v - mean_val) ** 2 for v in vals) / len(vals)
            disagreement_index = round(min(1.0, variance ** 0.5), 4)

            # ── volatility factor
            base_volatility = 0.02
            volatility_factor = base_volatility * (1.3 if disagreement_index > 0.6 else 1.0)
            volatility = round(volatility_factor, 6)

            # ── 3. Price movement
            price_movement = round(agg * volatility_factor, 6)

            # ── Determine observed direction
            if price_movement > 0:
                obs_direction = "up"
            elif price_movement < 0:
                obs_direction = "down"
            else:
                obs_direction = "flat"

            # ── Trend break detection (momentum archetype rule)
            trend_break_detected = False
            if prev_direction is not None and obs_direction != "flat":
                if obs_direction != (prev_direction if prev_direction != "flat" else obs_direction):
                    consecutive_break_days += 1
                else:
                    consecutive_break_days = 0

                # Check forecast vs observed for 2 consecutive days
                forecast_obs = "up" if forecast_is_up else "down"
                if obs_direction != forecast_obs:
                    consecutive_break_days_forecast = consecutive_break_days  # reuse counter
                    if consecutive_break_days_forecast >= 2:
                        trend_break_detected = True
            else:
                consecutive_break_days = 0

            if trend_break_detected:
                trend_breaks.append(day)

            # ── crowd conviction
            crowd_conviction = abs(agg)

            # ── sentiment momentum
            sentiment_momentum = agg - prev_sentiment

            # ── 4. Update each agent
            by_archetype: dict[str, list] = {}
            for s in state:
                by_archetype.setdefault(s["archetype"], []).append(s)

            for s in state:
                arch = s["archetype"]
                if arch == "retail":
                    new_strength = s["bias_strength"] * 0.7 + abs(sentiment_momentum) * 0.3
                    if s["reddit_influence_score"] > 1.2:
                        new_strength *= 1.15
                    s["bias_strength"] = round(min(1.0, new_strength), 4)
                    # bias direction follows momentum
                    if sentiment_momentum != 0:
                        s["bias_val"] = 1.0 if sentiment_momentum > 0 else -1.0

                elif arch == "institutional":
                    forecast_signal = 1.0 if forecast_is_up else -1.0
                    if agg * forecast_signal > 0:
                        # aligned
                        s["memory"]["confidence"] = min(1.0, s["memory"]["confidence"] + 0.05)
                    else:
                        # diverging
                        s["memory"]["confidence"] = max(0.0, s["memory"]["confidence"] - 0.05)

                elif arch == "momentum":
                    # amplify if same direction 2+ days
                    same_dir = (prev_direction == obs_direction) and (obs_direction != "flat")
                    if same_dir:
                        s["bias_strength"] = round(min(1.0, s["bias_strength"] * 1.2), 4)
                    # Use TimesFM direction as guide
                    if forecast_is_up and price_movement > 0:
                        s["bias_strength"] = round(min(1.0, s["bias_strength"] * 1.1), 4)
                    elif not forecast_is_up and price_movement < 0:
                        s["bias_strength"] = round(min(1.0, s["bias_strength"] * 1.1), 4)
                    if trend_break_detected:
                        s["bias_val"] = obs_direction == "up" and 1.0 or -1.0
                    else:
                        s["bias_val"] = agg

                elif arch == "contrarian":
                    if crowd_conviction > 0.7:
                        s["bias_val"] = -agg
                        s["bias_strength"] = round(min(1.0, s["bias_strength"] + 0.1), 4)

            # ── 5. Agent influence spreading
            # Top 10% by influence spread to 3 random same-archetype agents
            sorted_by_influence = sorted(state, key=lambda s: s["memory"]["influence"], reverse=True)
            top_10pct_count = max(1, len(sorted_by_influence) // 10)
            top_influencers_all = sorted_by_influence[:top_10pct_count]

            for spreader in top_influencers_all:
                arch = spreader["archetype"]
                same_arch = [s for s in by_archetype.get(arch, []) if s["id"] != spreader["id"]]
                targets = random.sample(same_arch, min(3, len(same_arch)))
                for target in targets:
                    target["bias_strength"] = round(
                        min(1.0, 0.8 * target["bias_strength"] + 0.2 * spreader["bias_strength"]),
                        4,
                    )

            # ── 6. Update memory
            for s in state:
                # Append today's decision
                bias_str = "bullish" if s["bias_val"] > 0 else ("bearish" if s["bias_val"] < 0 else "neutral")
                s["memory"]["decisions"].append({
                    "day": day,
                    "bias": bias_str,
                    "sentiment": round(s["bias_val"] * s["bias_strength"], 4),
                })

                # On days 2+: check if prior day prediction was correct
                if day >= 2 and s["memory"]["decisions"]:
                    prior = s["memory"]["decisions"][-2] if len(s["memory"]["decisions"]) >= 2 else None
                    if prior is not None:
                        prior_bullish = prior["bias"] == "bullish"
                        price_went_up = price_movement > 0
                        correct = (prior_bullish and price_went_up) or (not prior_bullish and not price_went_up)
                        s["memory"]["outcomes"].append({
                            "day": day,
                            "price_change": price_movement,
                            "correct": correct,
                        })
                        if correct:
                            s["memory"]["confidence"] = min(1.0, s["memory"]["confidence"] + 0.05)
                            s["memory"]["influence"] = min(5.0, s["memory"]["influence"] + 0.1)
                        else:
                            s["memory"]["confidence"] = max(0.0, s["memory"]["confidence"] - 0.05)
                            s["memory"]["influence"] = max(0.1, s["memory"]["influence"] - 0.1)

            # ── compute buy/sell pressure
            buy_agents = [s for s in state if s["bias_val"] > 0]
            sell_agents = [s for s in state if s["bias_val"] < 0]
            buy_pressure = round(sum(s["bias_val"] * s["bias_strength"] * s["weight"] for s in buy_agents), 4)
            sell_pressure = round(sum(abs(s["bias_val"]) * s["bias_strength"] * s["weight"] for s in sell_agents), 4)

            # ── dominant archetype
            arch_scores: dict[str, float] = {}
            for s in state:
                a = s["archetype"]
                arch_scores[a] = arch_scores.get(a, 0.0) + s["bias_val"] * s["bias_strength"] * s["weight"]
            dominant = max(arch_scores, key=lambda k: abs(arch_scores[k]))

            # ── top 3 influencers this day
            top3 = sorted(state, key=lambda s: s["memory"]["influence"], reverse=True)[:3]
            top_influencers = [
                {
                    "id": s["id"],
                    "influence": round(s["memory"]["influence"], 4),
                    "bias": "bullish" if s["bias_val"] > 0 else ("bearish" if s["bias_val"] < 0 else "neutral"),
                }
                for s in top3
            ]

            # ── update price trajectory
            price_index = round(price_index * (1 + price_movement), 4)
            price_trajectory.append(price_index)

            if initial_forecast_sentiment is None:
                initial_forecast_sentiment = agg

            sentiment_per_day.append({
                "day": day,
                "sentiment_score": agg,
                "price_movement": price_movement,
                "buy_pressure": buy_pressure,
                "sell_pressure": sell_pressure,
                "dominant_archetype": dominant,
                "trend_break_detected": trend_break_detected,
                "volatility": volatility,
                "top_influencers": top_influencers,
            })

            prev_sentiment = agg
            prev_direction = obs_direction

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

        # ── agent memory summary
        all_confidences = [s["memory"]["confidence"] for s in state]
        all_influences = [s["memory"]["influence"] for s in state]
        most_influential = max(state, key=lambda s: s["memory"]["influence"])
        agent_memory_summary = {
            "avg_confidence": round(sum(all_confidences) / len(all_confidences), 4),
            "avg_influence": round(sum(all_influences) / len(all_influences), 4),
            "most_influential_agent": most_influential["id"],
        }

        # ── forecast deviation
        forecast_deviation = round(
            abs(final_agg - (initial_forecast_sentiment or 0.0)), 4
        )

        return {
            "sentiment_per_day": sentiment_per_day,
            "final_direction": final_direction,
            "buy_sell_ratio": buy_sell_ratio,
            "top_reasons": top_reasons,
            "price_trajectory": price_trajectory,
            "forecast_deviation": forecast_deviation,
            "trend_breaks": trend_breaks,
            "agent_memory_summary": agent_memory_summary,
            "final_confidence": agent_memory_summary["avg_confidence"],
        }

    def generate_trade_signal(
        self,
        simulation_result: dict[str, Any],
        forecast: dict[str, Any],
        normalized: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = normalized.get("snapshot", {})
        sim_seed = normalized.get("simulation_seed", {})

        # Extract values
        forecast_direction = forecast.get("direction", "sideways")
        timesfm_up = forecast_direction == "up"

        reddit_sentiment = float(
            normalized.get("weighted_sentiment",
                snapshot.get("reddit_sentiment", 0.0))
        )

        # prediction market consensus
        consensus = float(snapshot.get("prediction_market_consensus", 0.5))

        # final simulation sentiment
        sentiment_per_day = simulation_result.get("sentiment_per_day", [])
        final_simulation_sentiment = (
            sentiment_per_day[-1]["sentiment_score"] if sentiment_per_day else 0.0
        )

        # disagreement index (last day volatility proxy)
        disagreement_index = 0.0
        if sentiment_per_day:
            last_day = sentiment_per_day[-1]
            # use volatility as a proxy for disagreement
            disagreement_index = last_day.get("volatility", 0.0) * 50  # scale to 0..1 range
            # also directly compute from price trajectory spread
            pt = simulation_result.get("price_trajectory", [100.0])
            if len(pt) > 1:
                spread = max(pt) - min(pt)
                disagreement_index = round(min(1.0, spread / 10.0), 4)

        # ── Divergence calculations
        timesfm_signal = 1.0 if timesfm_up else -1.0
        reddit_vs_timesfm = round(abs(reddit_sentiment - timesfm_signal) / 2.0, 4)
        agents_vs_prediction_markets = round(
            abs(final_simulation_sentiment - (consensus * 2.0 - 1.0)), 4
        )

        # ── Trade decision
        if disagreement_index > 0.65:
            trade = "HOLD"
            trade_type = "volatility"
            reason = (
                f"High disagreement index ({disagreement_index:.2f}) indicates market uncertainty. "
                f"Holding to avoid volatility-driven losses."
            )
        elif reddit_vs_timesfm > 0.4:
            trade_type = "reversal"
            if reddit_sentiment > timesfm_signal:
                trade = "CALL"
                reason = (
                    f"Reddit sentiment ({reddit_sentiment:+.2f}) significantly diverges above "
                    f"TimesFM signal ({timesfm_signal:+.0f}), suggesting retail-driven reversal opportunity. "
                    f"CALL on reversal play."
                )
            else:
                trade = "PUT"
                reason = (
                    f"Reddit sentiment ({reddit_sentiment:+.2f}) significantly diverges below "
                    f"TimesFM signal ({timesfm_signal:+.0f}), suggesting retail-driven reversal opportunity. "
                    f"PUT on reversal play."
                )
        elif reddit_vs_timesfm < 0.2 and agents_vs_prediction_markets < 0.2:
            trade_type = "trend"
            if timesfm_up:
                trade = "CALL"
                reason = (
                    f"Strong trend alignment: Reddit ({reddit_sentiment:+.2f}), "
                    f"TimesFM (up), and agents ({final_simulation_sentiment:+.2f}) all agree. "
                    f"Prediction markets confirm ({consensus:.0%}). CALL on trend continuation."
                )
            else:
                trade = "PUT"
                reason = (
                    f"Strong trend alignment: Reddit ({reddit_sentiment:+.2f}), "
                    f"TimesFM (down), and agents ({final_simulation_sentiment:+.2f}) all agree. "
                    f"Prediction markets confirm ({consensus:.0%}). PUT on trend continuation."
                )
        else:
            # Mixed signals — default to direction with lower confidence
            trade_type = "trend"
            if final_simulation_sentiment > 0:
                trade = "CALL"
            elif final_simulation_sentiment < 0:
                trade = "PUT"
            else:
                trade = "HOLD"
            reason = (
                f"Mixed signals: Reddit ({reddit_sentiment:+.2f}), "
                f"TimesFM ({'up' if timesfm_up else 'down'}), "
                f"agents ({final_simulation_sentiment:+.2f}). "
                f"Following simulation momentum."
            )

        # ── confidence
        final_confidence = simulation_result.get("final_confidence")
        if final_confidence is not None:
            confidence = round(float(final_confidence), 4)
        else:
            # avg of signal confidences
            timesfm_conf = float(forecast.get("confidence", 0.5))
            reddit_conf = min(1.0, abs(reddit_sentiment) + 0.3)
            agent_mem = simulation_result.get("agent_memory_summary", {})
            agent_conf = float(agent_mem.get("avg_confidence", 0.5))
            confidence = round((timesfm_conf + reddit_conf + agent_conf) / 3.0, 4)

        return {
            "trade": trade,
            "confidence": confidence,
            "reason": reason,
            "type": trade_type,
            "divergence": {
                "reddit_vs_timesfm": reddit_vs_timesfm,
                "agents_vs_prediction_markets": agents_vs_prediction_markets,
            },
        }

    # ──────────────────────────────────────────── prompt builders

    def _retail_prompt(
        self, seed: dict, snapshot: dict, reddit_features: dict | None = None
    ) -> str:
        rf = reddit_features or {}
        ret_summary = seed.get("retail_sentiment_summary", seed.get("sentiment_summary", "No retail sentiment available."))
        news_summary = seed.get("news_summary", "No news available.")
        bullish_pts = seed.get("key_bullish_points", [])
        bearish_pts = seed.get("key_bearish_points", [])
        disagreement = rf.get(
            "disagreement_index",
            seed.get("disagreement_score", seed.get("disagreement_level", 0.0)),
        )
        bullish_ratio = rf.get("bullish_ratio", 0.0)
        bearish_ratio = rf.get("bearish_ratio", 0.0)
        eng_velocity  = rf.get("engagement_velocity", 0.0)

        bull_text = "; ".join(str(p)[:80] for p in bullish_pts[:2]) if bullish_pts else "none"
        bear_text = "; ".join(str(p)[:80] for p in bearish_pts[:2]) if bearish_pts else "none"

        extra = ""
        if bullish_ratio or bearish_ratio:
            extra = (
                f"Reddit breakdown — bullish: {bullish_ratio:.0%}, "
                f"bearish: {bearish_ratio:.0%}. "
            )
        if eng_velocity:
            extra += f"Engagement velocity: {eng_velocity:.1f} avg score/post. "

        return (
            f"Retail perspective: {ret_summary} "
            f"{extra}"
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
