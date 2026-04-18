"""Simple keyword-based NLP for Reddit sentiment and feature extraction."""
from __future__ import annotations

import re
from typing import Any

_BULLISH = {
    "buy", "long", "bull", "bullish", "moon", "calls", "yolo", "beats",
    "upside", "breakout", "strong", "surge", "rally", "growth", "hold",
    "buy the dip", "undervalued", "squeeze", "rip", "green", "gainz",
}
_BEARISH = {
    "sell", "short", "bear", "bearish", "puts", "dump", "crash", "miss",
    "downside", "breakdown", "weak", "decline", "fall", "cut", "warning",
    "overvalued", "drop", "red", "rekt", "bag", "bagholder",
}
_UNCERTAINTY = {
    "maybe", "uncertain", "unclear", "wait", "confused", "risky",
    "volatile", "unsure", "depends", "idk", "hard to say",
}


def tokenize(text: str) -> list[str]:
    return re.findall(r"\b\w+\b", text.lower())


def score_text(text: str) -> dict[str, Any]:
    tokens = set(tokenize(text))
    bull = len(tokens & _BULLISH)
    bear = len(tokens & _BEARISH)
    unc  = len(tokens & _UNCERTAINTY)
    total = bull + bear + unc or 1
    sentiment = round((bull - bear) / (bull + bear + 1e-9), 3) if (bull + bear) > 0 else 0.0
    return {
        "bullish_signals": bull,
        "bearish_signals": bear,
        "uncertainty_signals": unc,
        "sentiment": round(max(-1.0, min(1.0, sentiment)), 3),
        "label": "bullish" if bull > bear else "bearish" if bear > bull else "neutral",
    }


def build_comment_tree(comments: list[dict]) -> list[dict]:
    """Convert flat comment list with parent_id into a nested tree."""
    by_id: dict[str, dict] = {}
    roots: list[dict] = []
    for c in comments:
        node = {**c, "replies": []}
        by_id[c["id"]] = node
    for c in comments:
        pid = c.get("parent_id", "")
        if pid and pid in by_id:
            by_id[pid]["replies"].append(by_id[c["id"]])
        else:
            roots.append(by_id[c["id"]])
    return roots


def extract_features(posts: list[dict]) -> dict[str, Any]:
    """Compute Reddit-specific numeric features from a list of post dicts."""
    if not posts:
        return _empty_features()

    total_comments = sum(p.get("comment_count", len(p.get("comments", []))) for p in posts)
    total_score    = sum(p.get("score", 0) for p in posts)

    bull_posts = sum(1 for p in posts if score_text(p.get("title", "") + " " + p.get("body", ""))["label"] == "bullish")
    bear_posts = sum(1 for p in posts if score_text(p.get("title", "") + " " + p.get("body", ""))["label"] == "bearish")
    n = len(posts)

    # Sentiment on all text combined
    all_text = " ".join(p.get("title", "") + " " + p.get("body", "") for p in posts)
    all_scores = [score_text(p.get("title","") + " " + p.get("body",""))["sentiment"] for p in posts]
    avg_sentiment = round(sum(all_scores) / n, 3)

    # Engagement velocity: avg score per post (proxy for spread rate)
    engagement_velocity = round(total_score / n, 2)

    # Disagreement index: how split are bullish vs bearish
    bull_ratio = round(bull_posts / n, 3)
    bear_ratio = round(bear_posts / n, 3)
    disagreement_index = round(1 - abs(bull_ratio - bear_ratio), 3)

    return {
        "post_count": n,
        "comment_count": total_comments,
        "bullish_ratio": bull_ratio,
        "bearish_ratio": bear_ratio,
        "avg_sentiment": avg_sentiment,
        "engagement_velocity": engagement_velocity,
        "disagreement_index": disagreement_index,
        "top_subreddits": list({p.get("subreddit", "unknown") for p in posts}),
    }


def _empty_features() -> dict[str, Any]:
    return {
        "post_count": 0,
        "comment_count": 0,
        "bullish_ratio": 0.0,
        "bearish_ratio": 0.0,
        "avg_sentiment": 0.0,
        "engagement_velocity": 0.0,
        "disagreement_index": 0.0,
        "top_subreddits": [],
    }
