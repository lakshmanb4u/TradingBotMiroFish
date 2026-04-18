"""SEC EDGAR fetcher.

Live path  : EDGAR full-text search + company facts API (no key required).
Fixture    : infra/fixtures/sec/<TICKER>.json
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

_FIXTURE_ROOT = Path(os.getenv("FIXTURE_ROOT", "infra/fixtures"))
_EDGAR_BASE = "https://efts.sec.gov/LATEST/search-index"
_EDGAR_SUBMISSIONS = "https://data.sec.gov/submissions"
_EDGAR_CIK_LOOKUP = "https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&dateRange=custom&startdt={start}&enddt={end}&forms=10-K,10-Q,8-K"


def fetch(ticker: str, limit: int = 5) -> dict[str, Any]:
    try:
        return _fetch_live(ticker, limit)
    except Exception as exc:
        fixture = _load_fixture(ticker)
        fixture["provider_mode"] = "fixture_fallback"
        fixture["live_error"] = str(exc)
        return fixture


def _fetch_live(ticker: str, limit: int) -> dict[str, Any]:
    headers = {"User-Agent": "market-swarm-lab contact@example.com"}
    with httpx.Client(timeout=15.0, headers=headers) as client:
        # Step 1: resolve CIK
        cik_resp = client.get(
            "https://efts.sec.gov/LATEST/search-index?q=%22{t}%22&forms=10-K&dateRange=custom&startdt=2023-01-01&enddt=2030-01-01".format(t=ticker)
        )
        cik_resp.raise_for_status()
        hits = cik_resp.json().get("hits", {}).get("hits", [])
        cik = None
        for hit in hits:
            src = hit.get("_source", {})
            if ticker.upper() in src.get("display_names", [src.get("entity_name", "")]):
                cik = src.get("file_num") or src.get("entity_id")
                break

        # Step 2: full-text search for recent filings
        search_resp = client.get(
            f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&forms=10-K,10-Q,8-K&_source=period_of_report,display_names,form_type,file_date,entity_name,period_of_report"
        )
        search_resp.raise_for_status()
        raw_hits = search_resp.json().get("hits", {}).get("hits", [])[:limit]

    filings = [
        {
            "form": h["_source"].get("form_type", ""),
            "filed_at": h["_source"].get("file_date", ""),
            "entity": h["_source"].get("entity_name", ticker),
            "summary": f"{h['_source'].get('form_type','')} filed {h['_source'].get('file_date','')}",
            "risk_score": _risk_score(h["_source"].get("form_type", "")),
        }
        for h in raw_hits
    ]

    return {
        "ticker": ticker.upper(),
        "provider_mode": "edgar_live",
        "cik": cik,
        "filings": filings,
    }


def _load_fixture(ticker: str) -> dict[str, Any]:
    path = _FIXTURE_ROOT / "sec" / f"{ticker.upper()}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"ticker": ticker.upper(), "filings": []}


def _risk_score(form_type: str) -> float:
    return {"10-K": 0.15, "10-Q": 0.12, "8-K": 0.08}.get(form_type, 0.05)
