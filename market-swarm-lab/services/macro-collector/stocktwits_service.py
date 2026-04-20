"""StockTwits service: fetch public symbol stream for retail sentiment."""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import httpx

_ROOT = Path(__file__).resolve().parents[2]
_log = logging.getLogger(__name__)

_BASE = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"


class StockTwitsService:
    def collect(self, ticker: str) -> dict[str, Any]:
        ticker = ticker.upper()
        provider_mode = "stocktwits_live"
        messages: list[dict[str, Any]] = []

        try:
            url = _BASE.format(ticker=ticker)
            resp = httpx.get(url, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
            messages = payload.get("messages", [])
        except Exception as exc:
            _log.warning("StockTwits fetch failed for %s: %s", ticker, exc)
            messages, provider_mode = self._load_fixture(ticker)

        bullish_count = 0
        bearish_count = 0
        no_opinion_count = 0
        sample_messages: list[str] = []

        for msg in messages:
            sentiment_val = (
                (msg.get("entities") or {})
                .get("sentiment", {}) or {}
            ).get("basic")
            if sentiment_val == "Bullish":
                bullish_count += 1
            elif sentiment_val == "Bearish":
                bearish_count += 1
            else:
                no_opinion_count += 1
            body = msg.get("body", "")
            if body and len(sample_messages) < 5:
                sample_messages.append(body)

        total = bullish_count + bearish_count
        sentiment_score = round((bullish_count - bearish_count) / max(total, 1), 4)
        if sentiment_score > 0.1:
            sentiment_label = "bullish"
        elif sentiment_score < -0.1:
            sentiment_label = "bearish"
        else:
            sentiment_label = "neutral"

        # Persist raw
        today_str = date.today().strftime("%Y%m%d")
        raw_dir = _ROOT / "state" / "raw" / "stocktwits"
        raw_dir.mkdir(parents=True, exist_ok=True)
        try:
            (raw_dir / f"{ticker}_{today_str}.json").write_text(
                json.dumps({"ticker": ticker, "provider_mode": provider_mode, "messages": messages[:50]}, indent=2)
            )
        except Exception as exc:
            _log.warning("StockTwits persist failed: %s", exc)

        return {
            "ticker": ticker,
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "no_opinion_count": no_opinion_count,
            "sentiment_score": sentiment_score,
            "sentiment_label": sentiment_label,
            "message_volume": len(messages),
            "sample_messages": sample_messages,
            "provider_mode": provider_mode,
            "source_audit": {
                "stocktwits": {
                    "status": "live" if provider_mode == "stocktwits_live" else "fallback",
                    "provider": "stocktwits" if provider_mode == "stocktwits_live" else "fixture",
                    "record_count": len(messages),
                }
            },
        }

    def _load_fixture(self, ticker: str) -> tuple[list[dict[str, Any]], str]:
        fixture_path = _ROOT / "infra" / "fixtures" / "stocktwits" / f"{ticker}.json"
        if fixture_path.exists():
            try:
                data = json.loads(fixture_path.read_text())
                return data.get("messages", []), "fixture_fallback"
            except Exception:
                pass
        return [], "fixture_fallback"
