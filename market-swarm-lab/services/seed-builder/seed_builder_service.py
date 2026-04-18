"""
Seed builder service.

Takes a fully normalized bundle and produces a simulation seed:
{
  "fundamental_summary":       "...",
  "retail_sentiment_summary":  "...",
  "news_summary":              "...",
  "prediction_market_summary": "...",
  "timesfm_summary":           "...",
  "key_bullish_points":        [],
  "key_bearish_points":        [],
  "disagreement_level":        0.0,
}

Rules:
  SEC  → risks + growth signals
  Reddit → bullish vs bearish breakdown, disagreement preserved
  News → catalysts and sentiment
  Prediction markets → consensus probabilities
  TimesFM → direction + confidence summary
"""
from __future__ import annotations

from typing import Any


class SeedBuilderService:
    def build(
        self,
        ticker: str,
        normalized_bundle: dict[str, Any],
        forecast: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snap    = normalized_bundle["snapshot"]
        docs    = normalized_bundle.get("documents", [])
        sim_seed = normalized_bundle.get("simulation_seed", {})

        fundamental_summary     = self._fundamental_summary(ticker, snap, sim_seed)
        retail_sentiment_summary = self._retail_summary(snap, sim_seed, docs)
        news_summary            = self._news_summary(snap, docs)
        prediction_market_summary = self._prediction_summary(snap, sim_seed)
        timesfm_summary         = self._timesfm_summary(ticker, snap, forecast)
        bullish, bearish         = self._extract_bull_bear(docs, sim_seed)
        disagreement             = round(snap.get("reddit_disagreement_index", 0.0), 3)

        return {
            "ticker":                    ticker.upper(),
            "fundamental_summary":       fundamental_summary,
            "retail_sentiment_summary":  retail_sentiment_summary,
            "news_summary":              news_summary,
            "prediction_market_summary": prediction_market_summary,
            "timesfm_summary":           timesfm_summary,
            "key_bullish_points":        bullish[:5],
            "key_bearish_points":        bearish[:5],
            "disagreement_level":        disagreement,
            "agent_personas":            sim_seed.get("agent_personas", []),
        }

    # ─────────────────────── section builders

    def _fundamental_summary(
        self, ticker: str, snap: dict, sim_seed: dict
    ) -> str:
        sec_docs = [d for d in sim_seed.get("sec_digest", []) if d.get("summary")]
        risk = snap.get("sec_risk_score", 0.0)
        risk_label = "elevated" if risk > 0.15 else "moderate" if risk > 0.08 else "low"
        base = f"{ticker} SEC risk score is {risk_label} ({risk:.2f})."
        if sec_docs:
            items = "; ".join(f"[{d.get('form','')}] {d['summary'][:80]}" for d in sec_docs[:2])
            return base + f" Recent filings: {items}."
        return base + " No recent filings parsed."

    def _retail_summary(
        self, snap: dict, sim_seed: dict, docs: list[dict]
    ) -> str:
        sentiment   = snap.get("reddit_sentiment", 0.0)
        mentions    = snap.get("reddit_mentions", 0)
        bullish_r   = sim_seed.get("retail_sentiment", {}).get("bullish_ratio", 0.0)
        bearish_r   = sim_seed.get("retail_sentiment", {}).get("bearish_ratio", 0.0)
        disagreement = sim_seed.get("retail_sentiment", {}).get("disagreement", 0.0)
        label = "bullish" if sentiment > 0.05 else "bearish" if sentiment < -0.05 else "mixed"
        lines = [
            f"Reddit is {label} with avg sentiment {sentiment:+.2f} across {mentions} tracked mentions.",
            f"Bullish posts: {bullish_r:.0%}, bearish posts: {bearish_r:.0%}.",
        ]
        if disagreement > 0.6:
            lines.append(f"Community is significantly split (disagreement index {disagreement:.2f}) — retail conviction is low.")
        elif disagreement < 0.3:
            lines.append(f"Sentiment is largely one-directional (disagreement index {disagreement:.2f}) — retail has high conviction.")
        top_post = next((d for d in docs if d.get("type") == "reddit_post"), None)
        if top_post:
            lines.append(f'Top post: "{top_post["text"][:100]}" ({top_post["label"]})')
        return " ".join(lines)

    def _news_summary(self, snap: dict, docs: list[dict]) -> str:
        news_s = snap.get("news_sentiment", 0.0)
        news_docs = [d for d in docs if d.get("type") == "news_article"]
        label = "positive" if news_s > 0.1 else "negative" if news_s < -0.1 else "neutral"
        base = f"News sentiment is {label} ({news_s:+.2f})."
        catalysts = [d["text"][:80] for d in news_docs[:3] if d.get("text")]
        if catalysts:
            return base + " Catalysts: " + " | ".join(catalysts) + "."
        return base

    def _prediction_summary(self, snap: dict, sim_seed: dict) -> str:
        consensus = snap.get("prediction_market_consensus", 0.5)
        markets   = sim_seed.get("prediction_markets", [])
        label = "risk-on" if consensus >= 0.55 else "risk-off" if consensus <= 0.45 else "neutral"
        base = f"Prediction market consensus is {label} at {consensus:.0%}."
        if markets:
            contracts = "; ".join(
                f"{m.get('venue','?')}: {m.get('contract','?')[:60]} ({m.get('probability_yes',0.5):.0%} yes)"
                for m in markets[:3]
            )
            return base + f" Contracts: {contracts}."
        return base

    def _timesfm_summary(
        self, ticker: str, snap: dict, forecast: dict | None
    ) -> str:
        if not forecast:
            rsi = snap.get("latest_rsi", 50.0)
            return (
                f"No forecast available. {ticker} RSI at {rsi:.1f} — "
                f"{'overbought territory' if rsi > 70 else 'oversold territory' if rsi < 30 else 'neutral range'}."
            )
        direction  = forecast.get("direction", "sideways")
        confidence = forecast.get("confidence", 0.5)
        close_5d   = forecast.get("forecast_close_5d", snap.get("latest_close", 0.0))
        delta      = forecast.get("delta_5d", 0.0)
        provider   = forecast.get("provider_mode", "model")
        return (
            f"TimesFM ({provider}) projects {direction} over 5 days: "
            f"target {close_5d} ({delta:+.2f}), confidence {confidence:.0%}."
        )

    # ─────────────────────── bull/bear extraction

    def _extract_bull_bear(
        self, docs: list[dict], sim_seed: dict
    ) -> tuple[list[str], list[str]]:
        bullish: list[str] = []
        bearish: list[str] = []

        for d in docs:
            text = d.get("text", "")[:120]
            if not text:
                continue
            if d.get("label") == "bullish":
                bullish.append(f"[{d.get('type','?')} / {d.get('source','?')}] {text}")
            elif d.get("label") == "bearish":
                bearish.append(f"[{d.get('type','?')} / {d.get('source','?')}] {text}")

        # Supplement with prediction market priors
        for m in sim_seed.get("prediction_markets", []):
            prob = m.get("probability_yes", 0.5)
            contract = m.get("contract", "")[:60]
            if prob >= 0.6:
                bullish.append(f"[prediction_market] {contract} at {prob:.0%} yes")
            elif prob <= 0.4:
                bearish.append(f"[prediction_market] {contract} at {1 - prob:.0%} no")

        return bullish, bearish
