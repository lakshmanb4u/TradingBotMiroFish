"""
Reddit collector FastAPI app.

GET  /collect/reddit/subreddit?ticker=NVDA&limit=10
GET  /collect/reddit/thread?url=<post_url>
POST /collect/reddit/import-fixture    body: {ticker, fixture_json}
GET  /reddit/features?ticker=NVDA
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from reddit_collector_service import RedditCollectorService  # noqa: E402

_RAW_ROOT = Path("state/raw/reddit")
_RAW_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="market-swarm-lab-reddit-collector", version="0.1.0")
_service = RedditCollectorService()


def _save(ticker: str, payload: dict) -> str:
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = _RAW_ROOT / f"{ticker.upper()}_{ts}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "reddit-collector"}


@app.get("/collect/reddit/subreddit")
def collect_subreddit(
    ticker: str = Query(...),
    subreddits: str = Query(
        default="wallstreetbets,stocks,investing",
        description="Comma-separated subreddit names",
    ),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict:
    sub_list = [s.strip() for s in subreddits.split(",") if s.strip()]
    result = _service.collect_subreddit(ticker=ticker, subreddits=sub_list, limit=limit)
    result["stored_at"] = _save(ticker, result)
    return result


@app.get("/collect/reddit/thread")
def collect_thread(
    url: str = Query(..., description="Full Reddit post URL"),
) -> dict:
    result = _service.collect_thread(post_url=url)
    ticker = result.get("post", {}).get("subreddit", "thread")
    result["stored_at"] = _save(ticker, result)
    return result


class ImportFixtureRequest(BaseModel):
    ticker: str
    fixture_json: dict[str, Any] = Field(
        ...,
        description="Raw fixture JSON in the same shape as infra/fixtures/reddit/<TICKER>.json",
    )


@app.post("/collect/reddit/import-fixture")
def import_fixture(request: ImportFixtureRequest) -> dict:
    fixture_dir = Path("infra/fixtures/reddit")
    fixture_dir.mkdir(parents=True, exist_ok=True)
    path = fixture_dir / f"{request.ticker.upper()}.json"
    path.write_text(
        json.dumps(request.fixture_json, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {
        "status": "imported",
        "ticker": request.ticker.upper(),
        "fixture_path": str(path),
    }


@app.get("/reddit/features")
def reddit_features(ticker: str = Query(...)) -> dict:
    result = _service.features(ticker=ticker)
    result["stored_at"] = _save(ticker, result)
    return result
