"""
Normalizes raw Apify Reddit records into the canonical post/comment schema.

Apify's trudax/reddit-scraper actor returns posts with nested comment arrays.
This module flattens and normalizes them into the same field shapes used by
the OAuth live mode so downstream code is provider-agnostic.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from nlp import extract_features, score_text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize(
    raw_items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert raw Apify records into (posts, comments).

    Posts that embed a ``comments`` array have their comments recursively
    extracted and flattened into the returned comments list.
    """
    posts: list[dict[str, Any]] = []
    comments: list[dict[str, Any]] = []

    for item in raw_items:
        data_type = item.get("dataType", item.get("type", "post"))

        if data_type == "comment":
            c = _normalize_comment(item, post_id=item.get("postId", ""))
            if c:
                comments.append(c)
        else:
            post = _normalize_post(item)
            if post:
                posts.append(post)
            for raw_c in item.get("comments", []):
                if isinstance(raw_c, dict):
                    comments.extend(
                        _extract_nested_comments(raw_c, post_id=post["id"], depth=0)
                    )

    return posts, comments


def derive_features(
    posts: list[dict[str, Any]],
    comments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute derived features from normalized posts and comments.

    Calls ``extract_features`` from nlp.py and supplements with
    ``unique_author_count`` across both posts and comments.
    """
    feats = extract_features(posts)
    authors: set[str] = set()
    for p in posts:
        if p.get("author"):
            authors.add(p["author"])
    for c in comments:
        if c.get("author"):
            authors.add(c["author"])
    feats["unique_author_count"] = len(authors)
    return feats


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_created_utc(item: dict[str, Any]) -> float | None:
    for key in ("createdAt", "created_utc", "created", "publishedAt"):
        val = item.get(key)
        if val is None:
            continue
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            try:
                dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                return dt.timestamp()
            except ValueError:
                pass
    return None


def _normalize_post(item: dict[str, Any]) -> dict[str, Any] | None:
    post_id = item.get("id", "")
    if not post_id:
        return None

    title = item.get("title", "")
    body = item.get("body", item.get("selftext", item.get("text", "")))
    sentiment = score_text((title + " " + body).strip())

    subreddit = (
        item.get("subreddit")
        or item.get("community")
        or item.get("_fetched_subreddit", "unknown")
    )

    permalink = item.get("permalink", item.get("url", ""))
    if permalink and not permalink.startswith("http"):
        permalink = "https://reddit.com" + permalink

    return {
        "id": post_id,
        "subreddit": subreddit,
        "title": title,
        "body": body,
        "score": int(item.get("score", item.get("upvotes", 0))),
        "upvote_ratio": float(item.get("upvoteRatio", item.get("upvote_ratio", 0.5))),
        "comment_count": int(
            item.get("numComments", item.get("num_comments", item.get("commentsCount", 0)))
        ),
        "url": item.get("url", permalink),
        "permalink": permalink,
        "created_utc": _parse_created_utc(item),
        "author": item.get("author", item.get("username", item.get("user", "unknown"))),
        "sentiment": sentiment["sentiment"],
        "sentiment_label": sentiment["label"],
    }


def _normalize_comment(
    item: dict[str, Any],
    post_id: str = "",
    depth: int = 0,
) -> dict[str, Any] | None:
    comment_id = item.get("id", "")
    if not comment_id:
        return None

    body = item.get("body", item.get("text", ""))
    sentiment = score_text(body)

    return {
        "id": comment_id,
        "parent_id": item.get("parentId", item.get("parent_id", "")),
        "post_id": post_id or item.get("postId", item.get("post_id", "")),
        "author": item.get("author", item.get("username", "unknown")),
        "body": body,
        "score": int(item.get("score", item.get("upvotes", 0))),
        "created_utc": _parse_created_utc(item),
        "depth": depth or int(item.get("depth", 0)),
        "sentiment": sentiment["sentiment"],
        "sentiment_label": sentiment["label"],
    }


def _extract_nested_comments(
    item: dict[str, Any],
    post_id: str,
    depth: int,
) -> list[dict[str, Any]]:
    """Recursively flatten nested comments from Apify output."""
    result: list[dict[str, Any]] = []
    c = _normalize_comment(item, post_id=post_id, depth=depth)
    if c:
        result.append(c)
    for child in item.get("replies", item.get("comments", [])):
        if isinstance(child, dict):
            result.extend(_extract_nested_comments(child, post_id=post_id, depth=depth + 1))
    return result
