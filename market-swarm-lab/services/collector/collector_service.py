from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REDDIT_COLLECTOR_DIR = Path(__file__).resolve().parents[1] / "reddit-collector"
if str(REDDIT_COLLECTOR_DIR) not in sys.path:
    sys.path.append(str(REDDIT_COLLECTOR_DIR))

from reddit_collector_service import RedditCollectorService

_FETCHER_DIR = str(Path(__file__).resolve().parent / "fetchers")
if _FETCHER_DIR not in sys.path:
    sys.path.insert(0, _FETCHER_DIR)

import sec as sec_fetcher      # noqa: E402
import news as news_fetcher     # noqa: E402
import polymarket as poly_fetcher  # noqa: E402
import kalshi as kalshi_fetcher  # noqa: E402
import ohlcv as ohlcv_fetcher   # noqa: E402


class MultiSourceCollector:
    def __init__(self) -> None:
        self.root = Path(__file__).resolve().parents[2]
        self.fixture_root = self.root / "infra" / "fixtures"
        self.reddit = RedditCollectorService()

    def collect(self, ticker: str) -> dict[str, Any]:
        ticker = ticker.upper()
        reddit_data = self.reddit.collect(ticker)
        sec_data = sec_fetcher.fetch(ticker)
        news_data = news_fetcher.fetch(ticker)
        poly_data = poly_fetcher.fetch(ticker)
        kalshi_data = kalshi_fetcher.fetch(ticker)
        ohlcv_data = ohlcv_fetcher.fetch(ticker)

        # Merge Polymarket + Kalshi into a unified prediction_markets payload
        markets = poly_data.get("markets", []) + kalshi_data.get("markets", [])
        prediction_markets = {"ticker": ticker, "markets": markets}

        return {
            "ticker": ticker,
            "provider_modes": {
                "sec": sec_data.get("provider_mode", "fixture_fallback"),
                "news": news_data.get("provider_mode", "fixture_fallback"),
                "reddit": reddit_data.get("provider_mode", "fixture_fallback"),
                "polymarket": poly_data.get("provider_mode", "fixture_fallback"),
                "kalshi": kalshi_data.get("provider_mode", "fixture_fallback"),
                "market_data": ohlcv_data.get("provider_mode", "fixture_fallback"),
            },
            "sec_filings": sec_data,
            "news": news_data,
            "reddit": reddit_data,
            "prediction_markets": prediction_markets,
            "market_data": ohlcv_data,
        }

    def _load_fixture(self, source: str, ticker: str) -> dict[str, Any]:
        path = self.fixture_root / source / f"{ticker}.json"
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
