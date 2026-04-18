"""
Reddit collector service.

Modes (auto-selected):
  oauth_live     - REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET present and reachable
  fixture_fallback - credentials missing or API down; loads from infra/fixtures/reddit/
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import httpx

import sys as _sys
_HERE_RC = __file__
import os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(_HERE_RC)))
from nlp import build_comment_tree, extract_features, score_text

_FIXTURE_ROOT = Path(os.getenv("FIXTURE_ROOT", "infra/fixtures"))
_CLIENT_ID     = os.getenv("REDDIT_CLIENT_ID", "")
_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
_USER_AGENT    = os.getenv("REDDIT_USER_AGENT", "market-swarm-lab/0.1")

_SUBREDDITS = ["wallstreetbets", "stocks", "investing", "options", "SecurityAnalysis"]


class RedditCollectorService:
    def __init__(self) -> None:
        self._token: str | None = None

    # ──────────────────────────── public

    def collect(self, ticker: str) -> dict[str, Any]:
        """Legacy entry point used by MultiSourceCollector."""
        return self.collect_subreddit(ticker=ticker, subreddits=_SUBREDDITS, limit=10)

    def collect_subreddit(
        self,
        ticker: str,
        subreddits: list[str] | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        subreddits = subreddits or _SUBREDDITS
        if _CLIENT_ID and _CLIENT_SECRET:
            try:
                return self._live_subreddit(ticker, subreddits, limit)
            except Exception as exc:
                data = self._load_fixture(ticker)
                data["provider_mode"] = "fixture_fallback"
                data["live_error"] = str(exc)
                return data
        data = self._load_fixture(ticker)
        data["provider_mode"] = "fixture_fallback"
        return data

    def collect_thread(self, post_url: str) -> dict[str, Any]:
        """Fetch a single thread (post + all comments) by URL."""
        if _CLIENT_ID and _CLIENT_SECRET:
            try:
                return self._live_thread(post_url)
            except Exception as exc:
                return {"provider_mode": "error", "error": str(exc), "post_url": post_url, "post": {}, "comments": [], "comment_tree": []}
        return {"provider_mode": "fixture_fallback", "post_url": post_url, "post": {}, "comments": [], "comment_tree": []}

    def features(self, ticker: str) -> dict[str, Any]:
        data = self.collect(ticker)
        posts = data.get("threads", data.get("posts", []))
        feats = extract_features(posts)
        return {
            "ticker": ticker.upper(),
            "provider_mode": data.get("provider_mode"),
            "features": feats,
            "activity": data.get("activity", []),
        }

    # ──────────────────────────── live

    def _get_token(self) -> str:
        if self._token:
            return self._token
        auth = base64.b64encode(f"{_CLIENT_ID}:{_CLIENT_SECRET}".encode()).decode()
        with httpx.Client(timeout=10.0) as client:
            r = client.post(
                "https://www.reddit.com/api/v1/access_token",
                headers={"Authorization": f"Basic {auth}", "User-Agent": _USER_AGENT},
                data={"grant_type": "client_credentials"},
            )
            r.raise_for_status()
        self._token = r.json()["access_token"]
        return self._token

    def _api_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._get_token()}", "User-Agent": _USER_AGENT}

    def _live_subreddit(self, ticker: str, subreddits: list[str], limit: int) -> dict[str, Any]:
        headers = self._api_headers()
        threads: list[dict[str, Any]] = []
        with httpx.Client(timeout=12.0, headers=headers) as client:
            # Search across listed subreddits
            for sub in subreddits:
                r = client.get(
                    f"https://oauth.reddit.com/r/{sub}/search",
                    params={"q": ticker, "sort": "new", "limit": limit, "restrict_sr": 1},
                )
                if r.status_code == 429:
                    break  # rate limited — stop gracefully
                if not r.is_success:
                    continue
                for child in r.json().get("data", {}).get("children", []):
                    d = child.get("data", {})
                    text = (d.get("title", "") + " " + d.get("selftext", "")).strip()
                    sentiment = score_text(text)
                    threads.append({
                        "id": d.get("id"),
                        "subreddit": sub,
                        "title": d.get("title"),
                        "body": d.get("selftext", ""),
                        "score": d.get("score", 0),
                        "upvote_ratio": d.get("upvote_ratio", 0.5),
                        "comment_count": d.get("num_comments", 0),
                        "url": d.get("url"),
                        "permalink": "https://reddit.com" + d.get("permalink", ""),
                        "created_utc": d.get("created_utc"),
                        "sentiment": sentiment["sentiment"],
                        "sentiment_label": sentiment["label"],
                        "comments": [],
                    })

        activity = self._build_activity(threads)
        return {
            "ticker": ticker.upper(),
            "provider_mode": "oauth_live",
            "threads": threads,
            "activity": activity,
        }

    def _live_thread(self, post_url: str) -> dict[str, Any]:
        """Fetch a post and its comment tree."""
        headers = self._api_headers()
        api_url = post_url.rstrip("/") + ".json"
        with httpx.Client(timeout=15.0, headers=headers) as client:
            r = client.get(api_url, params={"limit": 200, "depth": 5})
            r.raise_for_status()
            listing = r.json()

        post_data  = listing[0]["data"]["children"][0]["data"]
        comment_raw = listing[1]["data"]["children"] if len(listing) > 1 else []
        comments = self._flatten_comments(comment_raw)
        tree = build_comment_tree(comments)
        text = post_data.get("title", "") + " " + post_data.get("selftext", "")
        sentiment = score_text(text)

        post = {
            "id": post_data.get("id"),
            "title": post_data.get("title"),
            "body": post_data.get("selftext", ""),
            "score": post_data.get("score", 0),
            "upvote_ratio": post_data.get("upvote_ratio", 0.5),
            "comment_count": post_data.get("num_comments", 0),
            "created_utc": post_data.get("created_utc"),
            "sentiment": sentiment["sentiment"],
            "sentiment_label": sentiment["label"],
        }
        return {
            "provider_mode": "oauth_live",
            "post_url": post_url,
            "post": post,
            "comments": comments,
            "comment_tree": tree,
        }

    def _flatten_comments(self, children: list, parent_id: str = "") -> list[dict]:
        out = []
        for child in children:
            if child.get("kind") != "t1":
                continue
            d = child["data"]
            text = d.get("body", "")
            sentiment = score_text(text)
            comment = {
                "id": d.get("id"),
                "parent_id": parent_id or d.get("parent_id", ""),
                "author": d.get("author"),
                "body": text,
                "score": d.get("score", 0),
                "created_utc": d.get("created_utc"),
                "depth": d.get("depth", 0),
                "sentiment": sentiment["sentiment"],
                "sentiment_label": sentiment["label"],
            }
            out.append(comment)
            replies = d.get("replies", {})
            if isinstance(replies, dict):
                out.extend(self._flatten_comments(
                    replies.get("data", {}).get("children", []),
                    parent_id=d.get("id"),
                ))
        return out

    def _build_activity(self, threads: list[dict]) -> list[dict]:
        if not threads:
            return []
        feats = extract_features(threads)
        return [{
            "date": "latest",
            "mentions": feats["post_count"],
            "comments": feats["comment_count"],
            "bullish_ratio": feats["bullish_ratio"],
            "bearish_ratio": feats["bearish_ratio"],
            "avg_sentiment": feats["avg_sentiment"],
            "engagement_velocity": feats["engagement_velocity"],
            "disagreement_index": feats["disagreement_index"],
        }]

    # ──────────────────────────── fixtures

    def _load_fixture(self, ticker: str) -> dict[str, Any]:
        path = _FIXTURE_ROOT / "reddit" / f"{ticker.upper()}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {"ticker": ticker.upper(), "threads": [], "activity": []}
