"""Risk engine: validates strategy signals and computes position sizing."""
from __future__ import annotations


class RiskEngineService:
    def evaluate(self, signal: dict, context: dict) -> dict:
        source_audit: dict = context.get("source_audit") or {}
        reddit: dict = context.get("reddit") or {}
        reddit_features: dict = reddit.get("features") or {}
        disagreement_index: float = float(reddit_features.get("disagreement_index", 0.0))

        horizon: str = signal.get("horizon", "1d")
        confidence: float = float(signal.get("confidence", 0.0))
        strategy_type: str = signal.get("strategy_type", "no_trade")
        trade: str = signal.get("trade", "HOLD")

        risk_notes: list[str] = []
        approved: bool = True
        adjusted_confidence: float = confidence

        # Rule 1: confidence threshold
        if confidence < 0.60:
            approved = False
            risk_notes.append("confidence below threshold (0.60)")

        # Rule 2: OHLCV fallback
        ohlcv_audit: dict = source_audit.get("ohlcv") or {}
        if ohlcv_audit.get("status") == "fallback":
            approved = False
            risk_notes.append("OHLCV data is fallback — live price required")

        # Rule 3: news fallback — reduce confidence
        news_audit: dict = source_audit.get("news") or {}
        if news_audit.get("status") == "fallback":
            risk_notes.append("News data is fallback — reduced confidence")
            adjusted_confidence = round(max(0.0, adjusted_confidence - 0.05), 4)

        # Rule 4: no_trade / HOLD
        if strategy_type == "no_trade" or trade == "HOLD":
            approved = False

        # Rule 5: extreme disagreement
        if disagreement_index > 0.7 and strategy_type != "volatility":
            approved = False
            risk_notes.append("Extreme disagreement — only volatility strategies allowed")

        # Position sizing
        if not approved or trade == "HOLD":
            position_size_pct: float = 0.0
        elif adjusted_confidence >= 0.80:
            position_size_pct = 0.75
        elif adjusted_confidence >= 0.70:
            position_size_pct = 0.50
        elif adjusted_confidence >= 0.60:
            position_size_pct = 0.25
        else:
            position_size_pct = 0.0

        # Stop loss / take profit by horizon
        sl_tp_map: dict[str, tuple[float, float]] = {
            "1h": (0.25, 0.50),
            "1d": (0.30, 0.60),
            "3d": (0.35, 0.80),
            "5d": (0.40, 1.00),
        }
        stop_loss_pct, take_profit_pct = sl_tp_map.get(horizon, (0.30, 0.60))

        return {
            "approved": approved,
            "position_size_pct": position_size_pct,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "max_hold_time": horizon,
            "risk_notes": risk_notes,
            "adjusted_confidence": adjusted_confidence,
        }
