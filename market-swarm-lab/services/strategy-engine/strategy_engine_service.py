"""Strategy engine: converts multi-source signals into actionable option trade signals."""
from __future__ import annotations


class StrategyEngineService:
    def generate_signal(
        self, ticker: str, context: dict, horizon: str = "1d"
    ) -> dict:
        timesfm: dict = context.get("timesfm") or {}
        divergence: dict = context.get("divergence") or {}
        price: dict = context.get("price") or {}
        reddit: dict = context.get("reddit") or {}
        news: dict = context.get("news") or {}
        simulation: dict = context.get("simulation") or {}
        source_audit: dict = context.get("source_audit") or {}

        reddit_features: dict = reddit.get("features") or {}

        tf_direction: str = timesfm.get("direction", "neutral")
        tf_confidence: float = float(timesfm.get("confidence", 0.5))
        tf_predicted_return: float = float(timesfm.get("predicted_return", 0.0))

        div_score: float = float(divergence.get("divergence_score", 0.5))
        align_score: float = float(divergence.get("alignment_score", 0.5))
        div_signal: str = divergence.get("signal", "mixed")

        bullish_ratio: float = float(reddit_features.get("bullish_ratio", 0.0))
        disagreement_index: float = float(reddit_features.get("disagreement_index", 0.0))

        rv5: float = float(price.get("rolling_volatility_5d", 0.0))
        momentum: float = float(price.get("momentum", 0.0))
        rsi_14: float = float(price.get("rsi_14", 50.0))

        sim_confidence: float = float(simulation.get("final_confidence", 0.5))

        # Source freshness fraction
        source_freshness = self._source_freshness(source_audit)

        # --- Signal rule evaluation (first match wins) ---
        trade: str
        strategy_type: str

        if (
            tf_direction == "bullish"
            and align_score >= 0.65
            and div_score < 0.35
        ):
            trade = "CALL"
            strategy_type = "trend"
            rule = "trend_confirmation_bullish"

        elif (
            tf_direction == "bearish"
            and align_score >= 0.65
            and div_score < 0.35
        ):
            trade = "PUT"
            strategy_type = "trend"
            rule = "trend_confirmation_bearish"

        elif (
            bullish_ratio < 0.35
            and tf_direction == "bullish"
            and div_signal == "reversal_candidate"
        ):
            trade = "CALL"
            strategy_type = "reversal"
            rule = "reversal_bullish"

        elif (
            bullish_ratio > 0.65
            and tf_direction == "bearish"
            and div_signal == "reversal_candidate"
        ):
            trade = "PUT"
            strategy_type = "reversal"
            rule = "reversal_bearish"

        elif div_score > 0.6 and rv5 > 0.25:
            trade = "HOLD"
            strategy_type = "volatility"
            rule = "volatility_play"

        else:
            trade = "HOLD"
            strategy_type = "no_trade"
            rule = "no_trade"

        # --- Confidence (weighted average) ---
        confidence = round(
            tf_confidence * 0.35
            + align_score * 0.25
            + sim_confidence * 0.25
            + source_freshness * 0.15,
            4,
        )

        # --- Expected move ---
        raw_predicted_return = float(timesfm.get("predicted_return", 0.0))
        if raw_predicted_return and momentum:
            expected_move_pct = round(raw_predicted_return * (1 + momentum), 4)
        else:
            expected_move_pct = round(raw_predicted_return, 4)

        # --- Direction mapping ---
        direction_map = {"CALL": "bullish", "PUT": "bearish", "HOLD": "neutral"}
        direction = direction_map[trade]

        # --- Drivers ---
        drivers: list[str] = []
        if tf_direction != "neutral":
            drivers.append(f"TimesFM {tf_direction} (conf {tf_confidence:.2f})")
        if align_score:
            drivers.append(f"Alignment score {align_score:.2f} — {strategy_type}")
        if bullish_ratio:
            drivers.append(f"Reddit {bullish_ratio:.0%} bullish")
        if rsi_14:
            drivers.append(f"RSI {rsi_14:.1f} — {'overbought' if rsi_14 > 70 else 'oversold' if rsi_14 < 30 else 'not overbought'}")
        news_strength: float = float(news.get("narrative_strength", 0.0))
        if news_strength > 0.5:
            drivers.append("Breaking news detected")

        # --- Risk flags ---
        risk_flags: list[str] = []
        if confidence < 0.60:
            risk_flags.append("Low confidence (<0.60)")
        for src_name, src_val in source_audit.items():
            if isinstance(src_val, dict) and src_val.get("status") == "fallback":
                risk_flags.append(f"Source fallback: {src_name}")
        if disagreement_index > 0.5:
            risk_flags.append(f"High disagreement index ({disagreement_index:.2f})")
        if news_strength < 0.3 and news:
            risk_flags.append("Low narrative strength")

        # --- Option plan ---
        option_plan = self._option_plan(horizon, trade, direction, confidence)

        # --- Reason ---
        reason = f"Rule: {rule}. Direction: {tf_direction}. Divergence score: {div_score:.2f}. Alignment: {align_score:.2f}."

        entry_style = "marketable_limit" if confidence >= 0.65 else "limit"

        return {
            "ticker": ticker.upper(),
            "horizon": horizon,
            "trade": trade,
            "strategy_type": strategy_type,
            "direction": direction,
            "confidence": confidence,
            "expected_move_pct": expected_move_pct,
            "reason": reason,
            "drivers": drivers,
            "risk_flags": risk_flags,
            "option_plan": option_plan,
            "underlying": ticker.upper(),
            "option_type": trade,
            "entry_style": entry_style,
            "thesis": strategy_type,
        }

    # ------------------------------------------------------------------ helpers

    def _source_freshness(self, source_audit: dict) -> float:
        if not source_audit:
            return 0.5
        statuses = [
            v.get("status") for v in source_audit.values() if isinstance(v, dict)
        ]
        if not statuses:
            return 0.5
        live_count = sum(1 for s in statuses if s == "live")
        return round(live_count / len(statuses), 4)

    def _option_plan(
        self, horizon: str, trade: str, direction: str, confidence: float
    ) -> dict:
        if confidence < 0.55 or trade == "HOLD":
            return {
                "expiry_days": 0,
                "strike_selection": "ATM",
                "holding_period": "none",
            }
        horizon_map: dict[str, dict] = {
            "1h": {"expiry_days": 0, "strike_selection": "ATM", "holding_period": "intraday"},
            "1d": {"expiry_days": 2, "strike_selection": "ATM", "holding_period": "overnight"},
            "3d": {
                "expiry_days": 4,
                "strike_selection": "ATM+1%" if direction == "bullish" else "ATM-1%",
                "holding_period": "multi-day",
            },
            "5d": {"expiry_days": 7, "strike_selection": "delta-based", "holding_period": "multi-day"},
        }
        return horizon_map.get(
            horizon,
            {"expiry_days": 2, "strike_selection": "ATM", "holding_period": "overnight"},
        )
