from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_FETCHER_DIR = str(_ROOT / "services" / "collector" / "fetchers")
if _FETCHER_DIR not in sys.path:
    sys.path.insert(0, _FETCHER_DIR)
import news as _news_fetcher

_log = logging.getLogger(__name__)

_NEWS_CONFIDENCE_MAP: dict[str, float] = {
    "newsapi_live": 1.0,
    "alpha_vantage_news_live": 0.7,
    "fixture_fallback": 0.2,
}

_NEWS_PROVIDER_MAP: dict[str, str] = {
    "newsapi_live": "newsapi",
    "alpha_vantage_news_live": "alpha_vantage",
    "fixture_fallback": "fixture",
}


class NewsCollectorService:
    def collect(self, ticker: str) -> dict[str, Any]:
        try:
            data = _news_fetcher.fetch(ticker, limit=20)
        except Exception as exc:
            _log.warning("NewsCollectorService fetch failed for %s: %s", ticker, exc)
            data = {"ticker": ticker.upper(), "provider_mode": "fixture_fallback", "articles": []}

        articles: list[dict] = data.get("articles", [])
        provider_mode: str = data.get("provider_mode", "fixture_fallback")

        headlines = [a.get("title", "") for a in articles if a.get("title")]
        summaries = [a.get("summary", "") for a in articles if a.get("summary")]

        bullish_themes = [
            a["title"]
            for a in articles
            if float(a.get("sentiment", 0.0)) > 0.1 and a.get("title")
        ]
        bearish_themes = [
            a["title"]
            for a in articles
            if float(a.get("sentiment", 0.0)) < -0.1 and a.get("title")
        ]

        sentiments = [float(a.get("sentiment", 0.0)) for a in articles]
        sentiment_score = round(sum(sentiments) / len(sentiments), 4) if sentiments else 0.0

        if sentiment_score > 0.1:
            sentiment_label = "bullish"
        elif sentiment_score < -0.1:
            sentiment_label = "bearish"
        else:
            sentiment_label = "neutral"

        source_audit: dict[str, Any] = {
            "news": {
                "status": "live" if provider_mode in ("newsapi_live", "alpha_vantage_news_live") else "fallback",
                "provider": _NEWS_PROVIDER_MAP.get(provider_mode, "fixture"),
                "record_count": len(articles),
                "sample_headlines": headlines[:3],
            }
        }

        return {
            "ticker": ticker.upper(),
            "provider_mode": provider_mode,
            "articles": articles,
            "headlines": headlines,
            "summaries": summaries,
            "bullish_themes": bullish_themes,
            "bearish_themes": bearish_themes,
            "sentiment_score": sentiment_score,
            "sentiment_label": sentiment_label,
            "source_audit": source_audit,
        }

    def news_context(self, ticker: str) -> dict[str, Any]:
        data = self.collect(ticker)
        return {
            "bullish_themes": data["bullish_themes"],
            "bearish_themes": data["bearish_themes"],
            "sentiment_score": data["sentiment_score"],
            "sentiment_label": data["sentiment_label"],
            "headlines": data["headlines"],
            "provider_mode": data["provider_mode"],
        }
