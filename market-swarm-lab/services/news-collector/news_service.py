"""News service: fetch, normalize, feature extraction, persistence."""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_SERVICE_DIR = Path(__file__).resolve().parent
if str(_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICE_DIR))

from newsapi_client import NewsAPIClient

_ROOT = Path(__file__).resolve().parents[2]
_log = logging.getLogger(__name__)

_POSITIVE = {"beat", "surge", "rally", "growth", "strong", "bull", "buy", "up", "gain", "rise", "soar"}
_NEGATIVE = {"miss", "decline", "fall", "weak", "bear", "sell", "cut", "down", "warn", "drop", "crash"}


def _simple_sentiment(text: str) -> float:
    words = set(text.lower().split())
    pos = len(words & _POSITIVE)
    neg = len(words & _NEGATIVE)
    return round((pos - neg) / max(pos + neg, 1), 3)


class NewsService:
    def __init__(self) -> None:
        self._no_key = False
        self._client: NewsAPIClient | None = None
        try:
            self._client = NewsAPIClient()
        except ValueError:
            self._no_key = True

    def collect(self, ticker: str) -> dict[str, Any]:
        ticker = ticker.upper()
        provider_mode = "newsapi_live"
        raw_articles: list[dict[str, Any]] = []

        # Step 1: fetch
        if self._no_key or self._client is None:
            raw_articles, provider_mode = self._load_fixture(ticker)
        else:
            try:
                raw_articles = self._client.fetch_everything(ticker, days_back=5, page_size=20)
                provider_mode = "newsapi_live"
            except Exception as exc:
                _log.warning("NewsAPIClient.fetch_everything failed for %s: %s", ticker, exc)
                raw_articles, provider_mode = self._load_fixture(ticker)

        # Step 2: normalize
        now = datetime.now(timezone.utc)
        articles: list[dict[str, Any]] = []
        for a in raw_articles:
            title = a.get("title") or ""
            description = a.get("description") or a.get("summary") or ""
            text_for_sentiment = title + " " + description
            sentiment = _simple_sentiment(text_for_sentiment)

            published_at = a.get("published_at") or a.get("publishedAt") or ""
            breaking_news = False
            if published_at:
                try:
                    pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                    breaking_news = (now - pub_dt).total_seconds() < 86400
                except Exception:
                    pass

            articles.append({
                "title": title,
                "description": description,
                "source": a.get("source") or "",
                "url": a.get("url") or "",
                "published_at": published_at,
                "sentiment": sentiment,
                "breaking_news": breaking_news,
            })

        # Step 3: compute features
        bullish_count = sum(1 for a in articles if a["sentiment"] > 0.1)
        bearish_count = sum(1 for a in articles if a["sentiment"] < -0.1)
        neutral_count = len(articles) - bullish_count - bearish_count
        sentiment_score = round(
            sum(a["sentiment"] for a in articles) / max(len(articles), 1), 4
        )

        if sentiment_score > 0.1:
            sentiment_label = "bullish"
        elif sentiment_score < -0.1:
            sentiment_label = "bearish"
        else:
            sentiment_label = "neutral"

        narrative_strength = round(
            abs(sentiment_score) * min(len(articles), 20) / 20.0, 4
        )
        breaking_news_flag = any(a["breaking_news"] for a in articles)

        # Step 4: build output summary
        first_headline = articles[0]["title"] if articles else "N/A"
        news_summary = (
            f"News sentiment for {ticker} is {sentiment_label} (score: {sentiment_score:.2f}). "
            f"{bullish_count} bullish, {bearish_count} bearish, {neutral_count} neutral articles. "
            f"Top story: {first_headline}."
        )

        bullish_sorted = sorted(
            [a for a in articles if a["sentiment"] > 0.1],
            key=lambda x: x["sentiment"], reverse=True,
        )
        bearish_sorted = sorted(
            [a for a in articles if a["sentiment"] < -0.1],
            key=lambda x: x["sentiment"],
        )
        bullish_points = [a["title"] for a in bullish_sorted[:5]]
        bearish_points = [a["title"] for a in bearish_sorted[:5]]

        # Step 5: persist
        today_str = date.today().strftime("%Y%m%d")

        raw_dir = _ROOT / "state" / "raw" / "news"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / f"{ticker}_{today_str}.json"
        try:
            with open(raw_path, "w") as f:
                json.dump(
                    {"ticker": ticker, "provider_mode": provider_mode, "articles": raw_articles},
                    f, indent=2,
                )
        except Exception as exc:
            _log.warning("Raw news persist failed: %s", exc)

        norm_dir = _ROOT / "data" / "market_data" / "news"
        norm_dir.mkdir(parents=True, exist_ok=True)
        norm_path = norm_dir / f"{ticker}.json"
        try:
            with open(norm_path, "w") as f:
                json.dump(articles, f, indent=2)
        except Exception as exc:
            _log.warning("Normalized news persist failed: %s", exc)

        headlines = [a["title"] for a in articles if a["title"]]
        summaries = [a["description"] for a in articles if a["description"]]
        bullish_themes = [a["title"] for a in articles if a["sentiment"] > 0.1 and a["title"]]
        bearish_themes = [a["title"] for a in articles if a["sentiment"] < -0.1 and a["title"]]

        source_audit: dict[str, Any] = {
            "news": {
                "status": "live" if provider_mode == "newsapi_live" else "fallback",
                "provider": (
                    "newsapi" if provider_mode == "newsapi_live"
                    else ("alpha_vantage" if "alpha_vantage" in provider_mode else "fixture")
                ),
                "record_count": len(articles),
                "sample_headlines": headlines[:3],
            }
        }

        # Step 6: return
        return {
            "ticker": ticker,
            "provider_mode": provider_mode,
            "articles": articles,
            "headlines": headlines,
            "summaries": summaries,
            "bullish_themes": bullish_themes,
            "bearish_themes": bearish_themes,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "neutral_count": neutral_count,
            "sentiment_score": sentiment_score,
            "sentiment_label": sentiment_label,
            "narrative_strength": narrative_strength,
            "breaking_news_flag": breaking_news_flag,
            "news_summary": news_summary,
            "bullish_points": bullish_points,
            "bearish_points": bearish_points,
            "raw_artifact_path": str(raw_path),
            "normalized_artifact_path": str(norm_path),
            "source_audit": source_audit,
        }

    def news_context(self, ticker: str) -> dict[str, Any]:
        data = self.collect(ticker)
        return {
            "news_summary": data["news_summary"],
            "bullish_points": data["bullish_points"],
            "bearish_points": data["bearish_points"],
            "sentiment_score": data["sentiment_score"],
            "narrative_strength": data["narrative_strength"],
        }

    def _load_fixture(self, ticker: str) -> tuple[list[dict[str, Any]], str]:
        fixture_path = _ROOT / "infra" / "fixtures" / "news" / f"{ticker}.json"
        if not fixture_path.exists():
            _log.warning("No news fixture found for %s at %s", ticker, fixture_path)
            return [], "fixture_fallback"
        try:
            data = json.loads(fixture_path.read_text(encoding="utf-8"))
        except Exception as exc:
            _log.warning("Failed to load news fixture for %s: %s", ticker, exc)
            return [], "fixture_fallback"
        return data.get("articles", []), "fixture_fallback"
