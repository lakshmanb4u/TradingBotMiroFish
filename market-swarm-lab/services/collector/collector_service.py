from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

REDDIT_COLLECTOR_DIR = Path(__file__).resolve().parents[1] / "reddit-collector"
if str(REDDIT_COLLECTOR_DIR) not in sys.path:
    sys.path.append(str(REDDIT_COLLECTOR_DIR))

from reddit_collector_service import RedditCollectorService


class MultiSourceCollector:
    def __init__(self) -> None:
        self.root = Path(__file__).resolve().parents[2]
        self.fixture_root = self.root / "infra" / "fixtures"
        self.reddit = RedditCollectorService()
        self.newsapi_key = os.getenv("NEWSAPI_API_KEY", os.getenv("NEWSAPI_KEY", ""))
        self.alpha_vantage_key = os.getenv("ALPHAVANTAGE_API_KEY", os.getenv("ALPHA_VANTAGE_API_KEY", ""))

    def collect(self, ticker: str) -> dict[str, Any]:
        ticker = ticker.upper()
        reddit = self.reddit.collect(ticker)
        sec = self._load_fixture("sec", ticker)
        prediction_markets = self._load_fixture("prediction_markets", ticker)
        market_data, market_provider = self._market_data(ticker)
        news, news_provider = self._news(ticker)

        return {
            "ticker": ticker,
            "provider_modes": {
                "sec": "fixture_fallback",
                "news": news_provider,
                "reddit": reddit.get("provider_mode", "fixture_fallback"),
                "prediction_markets": "fixture_fallback",
                "market_data": market_provider,
            },
            "sec_filings": sec,
            "news": news,
            "reddit": reddit,
            "prediction_markets": prediction_markets,
            "market_data": market_data,
        }

    def _load_fixture(self, source: str, ticker: str) -> dict[str, Any]:
        path = self.fixture_root / source / f"{ticker}.json"
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def _news(self, ticker: str) -> tuple[dict[str, Any], str]:
        if self.newsapi_key:
            try:
                with httpx.Client(timeout=10.0) as client:
                    response = client.get(
                        "https://newsapi.org/v2/everything",
                        params={"q": ticker, "sortBy": "publishedAt", "pageSize": 10, "apiKey": self.newsapi_key},
                    )
                    response.raise_for_status()
                    payload = response.json()
                return {
                    "ticker": ticker,
                    "articles": [
                        {
                            "source": article.get("source", {}).get("name"),
                            "title": article.get("title"),
                            "summary": article.get("description"),
                            "published_at": article.get("publishedAt"),
                        }
                        for article in payload.get("articles", [])
                    ],
                }, "newsapi_live"
            except Exception:
                pass
        return self._load_fixture("news", ticker), "fixture_fallback"

    def _market_data(self, ticker: str) -> tuple[dict[str, Any], str]:
        if self.alpha_vantage_key:
            try:
                with httpx.Client(timeout=10.0) as client:
                    response = client.get(
                        "https://www.alphavantage.co/query",
                        params={
                            "function": "TIME_SERIES_DAILY_ADJUSTED",
                            "symbol": ticker,
                            "outputsize": "compact",
                            "apikey": self.alpha_vantage_key,
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                    series = payload.get("Time Series (Daily)", {})
                    points = []
                    for date, values in list(series.items())[:15]:
                        points.append(
                            {
                                "date": date,
                                "open": float(values["1. open"]),
                                "high": float(values["2. high"]),
                                "low": float(values["3. low"]),
                                "close": float(values["4. close"]),
                                "volume": int(values["6. volume"]),
                            }
                        )
                return {"ticker": ticker, "series": list(reversed(points))}, "alpha_vantage_live"
            except Exception:
                pass
        return self._load_fixture("market_data", ticker), "fixture_fallback"
