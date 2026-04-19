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

_REDDIT_CONFIDENCE_MAP: dict[str, float] = {
    "apify_live": 1.0,
    "oauth_live": 0.6,
    "fixture_fallback": 0.2,
}

_NEWS_CONFIDENCE_MAP: dict[str, float] = {
    "newsapi_live": 1.0,
    "alpha_vantage_news_live": 0.7,
    "fixture_fallback": 0.2,
}


class SeedBuilderService:
    def build(
        self,
        ticker: str,
        normalized_bundle: dict[str, Any],
        forecast: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snap     = normalized_bundle.get("snapshot", {})
        docs     = normalized_bundle.get("documents", [])
        sim_seed = normalized_bundle.get("simulation_seed", {})

        # Features at top level (legacy path) or nested under "reddit" key (apify path)
        reddit_features: dict[str, Any] = normalized_bundle.get("features", {})

        # Dedicated reddit key injected by run-demo / apify path
        reddit_data = normalized_bundle.get("reddit", {})
        reddit_ctx: dict[str, Any] = {}
        if reddit_data.get("provider_mode"):
            if reddit_data.get("features"):
                reddit_features = {**reddit_features, **reddit_data["features"]}
            reddit_ctx = self.build_reddit_context(reddit_data)

        fundamental_summary = self._fundamental_summary(ticker, snap, sim_seed)

        if reddit_ctx.get("retail_sentiment_summary"):
            retail_sentiment_summary = reddit_ctx["retail_sentiment_summary"]
        else:
            retail_sentiment_summary = self._retail_summary(snap, sim_seed, docs, reddit_features)

        # Prefer live news from NewsCollectorService when present
        news_collected = normalized_bundle.get("news", {})
        news_provider_mode = news_collected.get("provider_mode", "")
        news_confidence = _NEWS_CONFIDENCE_MAP.get(news_provider_mode, 0.2)
        if news_collected.get("articles") or news_collected.get("headlines"):
            _bullish = news_collected.get("bullish_themes", [])
            _bearish = news_collected.get("bearish_themes", [])
            _score = news_collected.get("sentiment_score", 0.0)
            _label = news_collected.get("sentiment_label", "neutral")
            _heads = news_collected.get("headlines", [])[:3]
            _parts = [f"News sentiment is {_label} (score: {_score:+.3f})."]
            if _bullish:
                _parts.append(f"Bullish catalysts: {'; '.join(h[:70] for h in _bullish[:2])}.")
            if _bearish:
                _parts.append(f"Bearish risks: {'; '.join(h[:70] for h in _bearish[:2])}.")
            if _heads:
                _parts.append(f"Headlines: {' | '.join(h[:60] for h in _heads)}.")
            news_summary = " ".join(_parts)
        else:
            news_summary = self._news_summary(snap, docs)
        prediction_market_summary = self._prediction_summary(snap, sim_seed)
        timesfm_summary           = self._timesfm_summary(ticker, snap, forecast)
        bullish, bearish          = self._extract_bull_bear(docs, sim_seed)

        provider_mode = reddit_data.get("provider_mode", "")
        if provider_mode in ("apify_live", "oauth_live"):
            reddit_bullish = reddit_ctx.get("key_bullish_points", [])
            reddit_bearish = reddit_ctx.get("key_bearish_points", [])
            bullish = reddit_bullish + [b for b in bullish if b not in reddit_bullish]
            bearish = reddit_bearish + [b for b in bearish if b not in reddit_bearish]

        disagreement = round(
            reddit_ctx.get(
                "disagreement_level",
                reddit_features.get(
                    "disagreement_index",
                    snap.get("reddit_disagreement_index", 0.0),
                ),
            ),
            3,
        )

        result: dict[str, Any] = {
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
        if reddit_ctx:
            result["reddit_confidence"] = reddit_ctx.get("reddit_confidence", 0.2)
            result["most_upvoted_arguments"] = reddit_ctx.get("most_upvoted_arguments", [])
        if news_provider_mode:
            result["news_confidence"] = news_confidence
            result["news_sentiment_label"] = news_collected.get("sentiment_label", "neutral")
            result["news_sentiment_score"] = news_collected.get("sentiment_score", 0.0)
            if news_collected.get("bullish_themes"):
                result["news_bullish_themes"] = news_collected["bullish_themes"][:5]
            if news_collected.get("bearish_themes"):
                result["news_bearish_themes"] = news_collected["bearish_themes"][:5]
        return result

    # ─────────────────────── reddit context builder

    def build_reddit_context(self, reddit_data: dict[str, Any]) -> dict[str, Any]:
        threads: list[dict] = reddit_data.get("threads", [])
        features: dict = reddit_data.get("features", {})
        provider_mode: str = reddit_data.get("provider_mode", "fixture_fallback")

        sorted_threads = sorted(threads, key=lambda t: t.get("score", 0), reverse=True)
        most_upvoted_arguments = [
            {
                "title": t.get("title", ""),
                "score": t.get("score", 0),
                "sentiment_label": t.get("sentiment_label", "neutral"),
            }
            for t in sorted_threads[:3]
        ]

        key_bullish_points = [
            t.get("title", "") for t in threads if t.get("sentiment_label") == "bullish"
        ][:5]
        key_bearish_points = [
            t.get("title", "") for t in threads if t.get("sentiment_label") == "bearish"
        ][:5]

        disagreement_level = round(float(features.get("disagreement_index", 0.0)), 3)
        if not features.get("disagreement_index") and threads:
            sentiments = [float(t.get("sentiment", 0.0)) for t in threads]
            mean_s = sum(sentiments) / len(sentiments)
            variance = sum((s - mean_s) ** 2 for s in sentiments) / len(sentiments)
            disagreement_level = round(min(1.0, variance ** 0.5), 3)

        reddit_confidence = _REDDIT_CONFIDENCE_MAP.get(provider_mode, 0.2)
        bullish_ratio = float(features.get("bullish_ratio", 0.0))
        bearish_ratio = float(features.get("bearish_ratio", 0.0))
        engagement_velocity = float(features.get("engagement_velocity", 0.0))

        label = (
            "bullish" if bullish_ratio > bearish_ratio + 0.1
            else ("bearish" if bearish_ratio > bullish_ratio + 0.1 else "mixed")
        )
        retail_sentiment_summary = (
            f"Reddit sentiment is {label} with {bullish_ratio:.0%} bullish"
            f" and {bearish_ratio:.0%} bearish posts."
            f" Engagement velocity: {engagement_velocity:.1f}."
            f" Disagreement index: {disagreement_level:.2f}."
        )

        return {
            "retail_sentiment_summary": retail_sentiment_summary,
            "key_bullish_points":       key_bullish_points,
            "key_bearish_points":       key_bearish_points,
            "most_upvoted_arguments":   most_upvoted_arguments,
            "disagreement_level":       disagreement_level,
            "reddit_confidence":        reddit_confidence,
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
        self,
        snap: dict,
        sim_seed: dict,
        docs: list[dict],
        reddit_features: dict | None = None,
    ) -> str:
        rf = reddit_features or {}

        # Prefer new apify-sourced feature keys; fall back to snap/sim_seed
        sentiment    = rf.get("avg_sentiment", snap.get("reddit_sentiment", 0.0))
        mentions     = rf.get("post_count", snap.get("reddit_mentions", 0))
        bullish_r    = rf.get("bullish_ratio",
                              sim_seed.get("retail_sentiment", {}).get("bullish_ratio", 0.0))
        bearish_r    = rf.get("bearish_ratio",
                              sim_seed.get("retail_sentiment", {}).get("bearish_ratio", 0.0))
        neutral_r    = rf.get("neutral_ratio", max(0.0, 1.0 - bullish_r - bearish_r))
        disagreement = rf.get("disagreement_index",
                              sim_seed.get("retail_sentiment", {}).get("disagreement", 0.0))
        eng_velocity = rf.get("engagement_velocity", 0.0)
        unique_authors = rf.get("unique_author_count", 0)

        label = "bullish" if sentiment > 0.05 else "bearish" if sentiment < -0.05 else "mixed"
        lines = [
            f"Reddit is {label} with avg sentiment {sentiment:+.2f} across {mentions} tracked mentions.",
            f"Bullish posts: {bullish_r:.0%}, bearish posts: {bearish_r:.0%}, neutral: {neutral_r:.0%}.",
        ]
        if eng_velocity:
            lines.append(f"Engagement velocity: {eng_velocity:.1f} avg score/post.")
        if unique_authors:
            lines.append(f"Unique authors: {unique_authors}.")
        if disagreement > 0.6:
            lines.append(
                f"Community is significantly split (disagreement index {disagreement:.2f}) — retail conviction is low."
            )
        elif disagreement < 0.3:
            lines.append(
                f"Sentiment is largely one-directional (disagreement index {disagreement:.2f}) — retail has high conviction."
            )
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
