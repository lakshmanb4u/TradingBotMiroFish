"""NewsAPI REST client."""
from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import httpx


class NewsAPIClient:
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("NEWSAPI_API_KEY", "")
        if not self._api_key:
            raise ValueError("NEWSAPI_API_KEY is required")

    def fetch_everything(
        self,
        query: str,
        days_back: int = 5,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        from_date = (date.today() - timedelta(days=days_back)).isoformat()
        with httpx.Client(timeout=10.0) as client:
            r = client.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "sortBy": "publishedAt",
                    "pageSize": page_size,
                    "from": from_date,
                    "apiKey": self._api_key,
                },
            )
            r.raise_for_status()
        return [self._normalize(a) for a in r.json().get("articles", [])]

    def fetch_top_headlines(
        self,
        query: str,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(
                "https://newsapi.org/v2/top-headlines",
                params={
                    "q": query,
                    "pageSize": page_size,
                    "apiKey": self._api_key,
                },
            )
            r.raise_for_status()
        return [self._normalize(a) for a in r.json().get("articles", [])]

    def _normalize(self, a: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": a.get("title") or "",
            "description": a.get("description") or "",
            "source": (a.get("source") or {}).get("name") or "",
            "url": a.get("url") or "",
            "published_at": a.get("publishedAt") or "",
            "content": a.get("content") or "",
        }
