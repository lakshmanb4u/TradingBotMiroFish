"""News fetcher — NewsAPI primary, Alpha Vantage news secondary, fixture fallback."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

_FIXTURE_ROOT = Path(os.getenv("FIXTURE_ROOT", "infra/fixtures"))
_NEWSAPI_KEY = os.getenv("NEWSAPI_API_KEY", "")
_AV_KEY = os.getenv("ALPHAVANTAGE_API_KEY", "")

_POSITIVE = {"beat", "surge", "rally", "growth", "strong", "bull", "buy", "up"}
_NEGATIVE = {"miss", "decline", "fall", "weak", "bear", "sell", "cut", "down", "warn"}


def fetch(ticker: str, limit: int = 10) -> dict[str, Any]:
    if _NEWSAPI_KEY:
        try:
            return _newsapi(ticker, limit)
        except Exception:
            pass
    if _AV_KEY:
        try:
            return _alpha_vantage(ticker, limit)
        except Exception:
            pass
    data = _load_fixture(ticker)
    data["provider_mode"] = "fixture_fallback"
    return data


def _newsapi(ticker: str, limit: int) -> dict[str, Any]:
    with httpx.Client(timeout=10.0) as client:
        r = client.get(
            "https://newsapi.org/v2/everything",
            params={"q": ticker, "sortBy": "publishedAt", "pageSize": limit, "apiKey": _NEWSAPI_KEY},
        )
        r.raise_for_status()
    articles = [
        {
            "source": a.get("source", {}).get("name"),
            "title": a.get("title"),
            "summary": a.get("description"),
            "url": a.get("url"),
            "published_at": a.get("publishedAt"),
            "sentiment": _simple_sentiment(a.get("title", "") + " " + (a.get("description") or "")),
        }
        for a in r.json().get("articles", [])
    ]
    return {"ticker": ticker.upper(), "provider_mode": "newsapi_live", "articles": articles}


def _alpha_vantage(ticker: str, limit: int) -> dict[str, Any]:
    with httpx.Client(timeout=10.0) as client:
        r = client.get(
            "https://www.alphavantage.co/query",
            params={"function": "NEWS_SENTIMENT", "tickers": ticker, "limit": limit, "apikey": _AV_KEY},
        )
        r.raise_for_status()
    articles = [
        {
            "source": item.get("source"),
            "title": item.get("title"),
            "summary": item.get("summary"),
            "url": item.get("url"),
            "published_at": item.get("time_published"),
            "sentiment": float(item.get("overall_sentiment_score", 0)),
        }
        for item in r.json().get("feed", [])
    ]
    return {"ticker": ticker.upper(), "provider_mode": "alpha_vantage_news_live", "articles": articles}


def _load_fixture(ticker: str) -> dict[str, Any]:
    path = _FIXTURE_ROOT / "news" / f"{ticker.upper()}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"ticker": ticker.upper(), "articles": []}


def _simple_sentiment(text: str) -> float:
    words = set(text.lower().split())
    pos = len(words & _POSITIVE)
    neg = len(words & _NEGATIVE)
    total = pos + neg
    if total == 0:
        return 0.0
    return round((pos - neg) / total, 3)
