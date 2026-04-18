from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import httpx


class RedditCollectorService:
    def __init__(self) -> None:
        self.client_id = os.getenv("REDDIT_CLIENT_ID", "")
        self.client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")
        self.user_agent = os.getenv("REDDIT_USER_AGENT", "market-swarm-lab-demo/0.1")
        self.fixture_dir = Path(__file__).resolve().parents[2] / "infra" / "fixtures" / "reddit"

    def collect(self, ticker: str) -> dict[str, Any]:
        ticker = ticker.upper()
        if self.client_id and self.client_secret:
            try:
                return self._collect_live(ticker)
            except Exception:
                pass
        return self._collect_fixture(ticker)

    def _collect_live(self, ticker: str) -> dict[str, Any]:
        auth = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        headers = {
            "Authorization": f"Basic {auth}",
            "User-Agent": self.user_agent,
        }
        with httpx.Client(timeout=10.0) as client:
            token_response = client.post(
                "https://www.reddit.com/api/v1/access_token",
                headers=headers,
                data={"grant_type": "client_credentials"},
            )
            token_response.raise_for_status()
            token = token_response.json()["access_token"]
            api_headers = {
                "Authorization": f"Bearer {token}",
                "User-Agent": self.user_agent,
            }
            search_response = client.get(
                "https://oauth.reddit.com/search",
                headers=api_headers,
                params={
                    "q": ticker,
                    "sort": "new",
                    "limit": 10,
                    "type": "link",
                },
            )
            search_response.raise_for_status()
            children = search_response.json().get("data", {}).get("children", [])
            threads = []
            for item in children:
                data = item.get("data", {})
                threads.append(
                    {
                        "subreddit": data.get("subreddit"),
                        "title": data.get("title"),
                        "body": data.get("selftext", ""),
                        "score": data.get("score", 0),
                        "comments": [],
                    }
                )
        return {
            "provider_mode": "reddit_oauth",
            "ticker": ticker,
            "activity": self._derive_activity_from_threads(threads),
            "threads": threads,
        }

    def _collect_fixture(self, ticker: str) -> dict[str, Any]:
        fixture_path = self.fixture_dir / f"{ticker}.json"
        with fixture_path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        payload["provider_mode"] = "fixture_fallback"
        return payload

    def _derive_activity_from_threads(self, threads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        mentions = len(threads)
        bullish = sum(1 for thread in threads if any(word in (thread.get("title", "") + " " + thread.get("body", "")).lower() for word in ["buy", "long", "bull", "beat", "up"]))
        bearish = sum(1 for thread in threads if any(word in (thread.get("title", "") + " " + thread.get("body", "")).lower() for word in ["sell", "short", "bear", "miss", "down"]))
        total_comments = sum(len(thread.get("comments", [])) for thread in threads)
        sentiment = 0.15 if bullish >= bearish else -0.15
        return [
            {
                "date": "latest",
                "mentions": mentions,
                "comments": total_comments,
                "bullish_ratio": round(bullish / max(mentions, 1), 2),
                "bearish_ratio": round(bearish / max(mentions, 1), 2),
                "avg_sentiment": sentiment,
            }
        ]
