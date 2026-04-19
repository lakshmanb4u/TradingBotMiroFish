"""
Apify-backed Reddit data fetcher.

Calls the Apify REST API to run actor trudax/reddit-scraper and returns
raw post + comment records as a flat list of dicts. Each subreddit is
fetched as a separate actor run so failures are isolated.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

_APIFY_TOKEN = os.getenv("APIFY_API_TOKEN", "")
_ACTOR_ID = os.getenv("APIFY_REDDIT_ACTOR", "trudax/reddit-scraper")
_TIMEOUT = 120.0


def fetch_subreddits(subreddits: list[str], limit: int = 20) -> list[dict[str, Any]]:
    """Run the Apify Reddit scraper for each subreddit.

    Args:
        subreddits: Subreddit names without the r/ prefix.
        limit: Maximum posts per subreddit.

    Returns:
        Flat list of raw Apify post + comment records, each tagged with
        ``_fetched_subreddit`` for downstream normalisation.

    Raises:
        ValueError: If APIFY_API_TOKEN is not set.
        httpx.TimeoutException: If the actor run exceeds 120 s (caller should catch).
        httpx.HTTPStatusError: On non-2xx responses.
    """
    token = _APIFY_TOKEN
    if not token:
        raise ValueError("APIFY_API_TOKEN is not set")

    # Apify REST API uses ~ as owner/name separator in URL path segments.
    actor_slug = _ACTOR_ID.replace("/", "~")
    endpoint = (
        f"https://api.apify.com/v2/acts/{actor_slug}"
        "/run-sync-get-dataset-items"
    )

    all_items: list[dict[str, Any]] = []

    with httpx.Client(timeout=_TIMEOUT) as client:
        for sub in subreddits:
            payload: dict[str, Any] = {
                "startUrls": [{"url": f"https://www.reddit.com/r/{sub}/"}],
                "maxItems": limit,
                "sort": "hot",
                "includeComments": True,
                "maxComments": 50,
                "maxCommentsDepth": 3,
            }
            r = client.post(endpoint, params={"token": token}, json=payload)
            r.raise_for_status()
            items = r.json()
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        item.setdefault("_fetched_subreddit", sub)
                all_items.extend(items)

    return all_items
