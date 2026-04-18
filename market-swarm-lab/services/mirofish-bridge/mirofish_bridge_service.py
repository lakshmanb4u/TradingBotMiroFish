from __future__ import annotations

import os
from typing import Any

import httpx


class MiroFishBridgeService:
    def __init__(self) -> None:
        self.base_url = os.getenv("MIROFISH_BASE_URL", "").rstrip("/")

    def run(self, ticker: str, normalized_bundle: dict[str, Any], forecast: dict[str, Any]) -> dict[str, Any]:
        remote_status = self._remote_status()
        provider_mode = remote_status["provider_mode"]
        seed = self._build_seed_packet(ticker, normalized_bundle, forecast)
        local_result = self._run_local_simulation(ticker, normalized_bundle, forecast)
        local_result["provider_mode"] = provider_mode
        local_result["seed_packet"] = seed
        local_result["mirofish_remote"] = remote_status
        return local_result

    def _remote_status(self) -> dict[str, Any]:
        if not self.base_url:
            return {"provider_mode": "local_mirofish_fallback", "connected": False, "reason": "MIROFISH_BASE_URL not configured"}
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self.base_url}/health")
                response.raise_for_status()
            return {
                "provider_mode": "remote_seed_bridge_preview",
                "connected": True,
                "reason": "remote health check passed, seed packet prepared for external orchestration",
            }
        except Exception:
            return {
                "provider_mode": "mirofish_unreachable_fallback",
                "connected": False,
                "reason": "remote MiroFish unavailable, local fallback simulation used",
            }

    def _build_seed_packet(self, ticker: str, normalized_bundle: dict[str, Any], forecast: dict[str, Any]) -> dict[str, Any]:
        snapshot = normalized_bundle["snapshot"]
        seed = normalized_bundle["simulation_seed"]
        return {
            "ticker": ticker.upper(),
            "prompt": f"Simulate retail, analyst, and compliance reactions for {ticker.upper()} using Reddit as a first-class sentiment driver.",
            "context": {
                "latest_close": snapshot["latest_close"],
                "latest_rsi": snapshot["latest_rsi"],
                "reddit_sentiment": snapshot["reddit_sentiment"],
                "prediction_market_consensus": snapshot["prediction_market_consensus"],
                "forecast_close_5d": forecast["forecast_close_5d"],
            },
            "agents": seed["agent_personas"],
            "narratives": seed["key_narratives"],
            "reddit_threads": seed["retail_sentiment"]["subreddit_activity"][:5],
        }

    def _run_local_simulation(self, ticker: str, normalized_bundle: dict[str, Any], forecast: dict[str, Any]) -> dict[str, Any]:
        snapshot = normalized_bundle["snapshot"]
        delta = forecast["delta_5d"]
        base_score = (
            snapshot["reddit_sentiment"] * 35
            + (snapshot["prediction_market_consensus"] - 0.5) * 100
            + (0.5 if delta >= 0 else -0.5) * 20
            - max(0, snapshot["sec_risk_score"]) * 15
        )
        outlook_score = round(max(-100, min(100, base_score)), 2)
        regime = "bullish" if outlook_score >= 12 else "bearish" if outlook_score <= -12 else "rangebound"

        rounds = [
            {
                "round": 1,
                "focus": "Reddit opens the loop",
                "dominant_signal": "retail sentiment",
                "score": round(outlook_score * 0.6, 2),
            },
            {
                "round": 2,
                "focus": "News and filings reprice conviction",
                "dominant_signal": "news + SEC",
                "score": round(outlook_score * 0.85, 2),
            },
            {
                "round": 3,
                "focus": "Prediction markets and price action close the loop",
                "dominant_signal": "market + prediction markets",
                "score": outlook_score,
            },
        ]

        return {
            "ticker": ticker.upper(),
            "regime": regime,
            "outlook_score": outlook_score,
            "agent_summary": {
                "agent_count": len(normalized_bundle["simulation_seed"]["agent_personas"]),
                "retail_sentiment_score": snapshot["reddit_sentiment"],
                "prediction_market_consensus": snapshot["prediction_market_consensus"],
            },
            "rounds": rounds,
            "recommended_focus": (
                "lean long but watch retail overheating" if regime == "bullish" else
                "stay defensive, Reddit is not offsetting broader risk" if regime == "bearish" else
                "treat as a tactical tape, not a structural trend"
            ),
        }
