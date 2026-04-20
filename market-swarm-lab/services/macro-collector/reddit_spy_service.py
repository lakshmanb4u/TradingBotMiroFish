"""Reddit SPY service: multi-subreddit market sentiment via pullpush.io."""
from __future__ import annotations

import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

import httpx

_ROOT = Path(__file__).resolve().parents[2]
_log = logging.getLogger(__name__)

_REDDIT_NLP_DIR = str(_ROOT / "services" / "reddit-collector")
if _REDDIT_NLP_DIR not in sys.path:
    sys.path.insert(0, _REDDIT_NLP_DIR)

_PULLPUSH_BASE = "https://api.pullpush.io/reddit/search/submission/"
_SUBREDDITS = ["spy", "wallstreetbets", "stocks", "options"]

_MARKET_KEYWORDS = {
    "spy", "s&p", "market", "calls", "puts", "bull", "bear",
    "spx", "es", "index", "nasdaq", "fed", "macro",
}


def _is_relevant(title: str) -> bool:
    low = title.lower()
    return any(kw in low for kw in _MARKET_KEYWORDS)


class RedditSPYService:
    def collect(self, ticker: str = "SPY") -> dict[str, Any]:
        ticker = ticker.upper()
        provider_mode = "pullpush_live"
        all_posts: list[dict[str, Any]] = []
        subreddits_fetched: list[str] = []
        errors = 0

        for sub in _SUBREDDITS:
            try:
                resp = httpx.get(
                    _PULLPUSH_BASE,
                    params={"subreddit": sub, "size": 50, "sort": "desc", "sort_type": "score"},
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                posts = data.get("data", [])
                for p in posts:
                    p["_subreddit"] = sub
                all_posts.extend(posts)
                subreddits_fetched.append(sub)
            except Exception as exc:
                _log.warning("PullPush fetch failed for r/%s: %s", sub, exc)
                errors += 1

        if not all_posts and errors == len(_SUBREDDITS):
            all_posts, provider_mode = self._load_fixture(ticker)
        elif subreddits_fetched:
            provider_mode = "pullpush_live"

        # Score each post with NLP
        try:
            from nlp import score_text
        except ImportError:
            def score_text(text: str) -> dict[str, Any]:  # type: ignore[misc]
                return {"sentiment": 0.0, "label": "neutral"}

        scored: list[dict[str, Any]] = []
        relevant: list[dict[str, Any]] = []

        for p in all_posts:
            title = p.get("title", "")
            body = p.get("selftext", "") or ""
            full_text = title + " " + body
            nlp = score_text(full_text)
            entry = {
                "title": title,
                "score": int(p.get("score", 0)),
                "label": nlp["label"],
                "sentiment": float(nlp["sentiment"]),
                "subreddit": p.get("_subreddit") or p.get("subreddit", ""),
            }
            scored.append(entry)
            if _is_relevant(title):
                relevant.append(entry)

        total_posts = len(scored)
        relevant_posts = len(relevant)

        # Sentiment breakdown on relevant posts (fallback to all if none relevant)
        pool = relevant if relevant else scored
        if pool:
            bull = sum(1 for p in pool if p["label"] == "bullish")
            bear = sum(1 for p in pool if p["label"] == "bearish")
            neut = len(pool) - bull - bear
            n = len(pool)
            bullish_pct = round(bull / n, 4)
            bearish_pct = round(bear / n, 4)
            neutral_pct = round(neut / n, 4)
            avg_sentiment = round(sum(p["sentiment"] for p in pool) / n, 4)
        else:
            bullish_pct = bearish_pct = neutral_pct = avg_sentiment = 0.0

        if avg_sentiment > 0.1:
            sentiment_label = "bullish"
        elif avg_sentiment < -0.1:
            sentiment_label = "bearish"
        else:
            sentiment_label = "neutral"

        top_posts = sorted(scored, key=lambda p: p["score"], reverse=True)[:10]
        key_themes = [
            p["title"] for p in top_posts if p["label"] in ("bullish", "bearish")
        ][:6]

        # Persist raw
        today_str = date.today().strftime("%Y%m%d")
        raw_dir = _ROOT / "state" / "raw" / "reddit_spy"
        raw_dir.mkdir(parents=True, exist_ok=True)
        try:
            (raw_dir / f"{today_str}.json").write_text(
                json.dumps(
                    {"ticker": ticker, "provider_mode": provider_mode, "posts": all_posts[:100]},
                    indent=2,
                )
            )
        except Exception as exc:
            _log.warning("Reddit SPY persist failed: %s", exc)

        return {
            "ticker": ticker,
            "subreddits_fetched": subreddits_fetched,
            "total_posts": total_posts,
            "relevant_posts": relevant_posts,
            "bullish_pct": bullish_pct,
            "bearish_pct": bearish_pct,
            "neutral_pct": neutral_pct,
            "avg_sentiment": avg_sentiment,
            "sentiment_label": sentiment_label,
            "top_posts": top_posts,
            "key_themes": key_themes,
            "provider_mode": provider_mode,
            "source_audit": {
                "reddit_spy": {
                    "status": "live" if provider_mode == "pullpush_live" else "fallback",
                    "provider": "pullpush" if provider_mode == "pullpush_live" else "fixture",
                    "record_count": total_posts,
                }
            },
        }

    def _load_fixture(self, ticker: str) -> tuple[list[dict[str, Any]], str]:
        fixture_path = _ROOT / "infra" / "fixtures" / "reddit_spy" / f"{ticker}.json"
        if fixture_path.exists():
            try:
                data = json.loads(fixture_path.read_text())
                return data.get("posts", []), "fixture_fallback"
            except Exception:
                pass
        return [], "fixture_fallback"
