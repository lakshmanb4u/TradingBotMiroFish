"""Schwab Trader API client.

Endpoints:
  - get_quote(symbol)          → real-time quote
  - get_options_chain(symbol)  → full options chain (all strikes/expiries)
  - get_price_history(symbol)  → OHLCV history (default: daily, 1 year)

All calls auto-refresh the OAuth token via schwab_auth.get_valid_token().
Falls back to fixture data when token is missing (offline/market-closed mode).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import requests

from schwab_auth import get_valid_token

_log = logging.getLogger(__name__)

BASE_URL = os.environ.get("SCHWAB_BASE_URL", "https://api.schwabapi.com")
MARKETDATA_URL = f"{BASE_URL}/marketdata/v1"

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


class SchwabClient:
    """Thin wrapper around Schwab Trader API marketdata endpoints."""

    def __init__(self) -> None:
        self._session = requests.Session()

    def _headers(self) -> dict[str, str]:
        token = get_valid_token()
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        url = f"{MARKETDATA_URL}/{path}"
        resp = self._session.get(url, headers=self._headers(), params=params or {}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    # ── Quotes ────────────────────────────────────────────────────────────────

    def get_quote(self, symbol: str) -> dict[str, Any]:
        """Real-time quote for a single symbol (e.g. 'SPY')."""
        symbol = symbol.upper()
        try:
            data = self._get("quotes", params={"symbols": symbol})
            quote = data.get(symbol, data)
            last = (quote.get("quote") or quote.get("extended") or {}).get("lastPrice")
            _log.info("[schwab] quote %s: %s", symbol, last)
            return quote
        except Exception as exc:
            _log.warning("[schwab] get_quote failed for %s: %s — using fixture", symbol, exc)
            return self._fixture_quote(symbol)

    # ── Options Chain ──────────────────────────────────────────────────────────

    def get_options_chain(
        self,
        symbol: str,
        contract_type: str = "ALL",
        strike_count: int = 20,
        include_underlying_quote: bool = True,
    ) -> dict[str, Any]:
        """Full options chain for symbol.

        Args:
            symbol: Ticker (e.g. 'SPY')
            contract_type: 'CALL', 'PUT', or 'ALL'
            strike_count: Number of strikes above/below ATM
            include_underlying_quote: Include underlying asset quote
        """
        symbol = symbol.upper()
        try:
            data = self._get(
                "chains",
                params={
                    "symbol": symbol,
                    "contractType": contract_type,
                    "strikeCount": strike_count,
                    "includeUnderlyingQuote": str(include_underlying_quote).lower(),
                    "strategy": "SINGLE",
                },
            )
            _log.info(
                "[schwab] options chain %s: %d call strikes, %d put strikes",
                symbol,
                len(data.get("callExpDateMap", {})),
                len(data.get("putExpDateMap", {})),
            )
            return data
        except Exception as exc:
            _log.warning("[schwab] get_options_chain failed for %s: %s — using fixture", symbol, exc)
            return self._fixture_options_chain(symbol)

    # ── Price History ──────────────────────────────────────────────────────────

    def get_price_history(
        self,
        symbol: str,
        period_type: str = "year",
        period: int = 1,
        frequency_type: str = "daily",
        frequency: int = 1,
        need_extended_hours_data: bool = False,
    ) -> list[dict[str, Any]]:
        """OHLCV price history.

        Returns list of candles: [{datetime, open, high, low, close, volume}, ...]
        Default: 1 year of daily bars.
        """
        symbol = symbol.upper()
        try:
            data = self._get(
                f"pricehistory",
                params={
                    "symbol": symbol,
                    "periodType": period_type,
                    "period": period,
                    "frequencyType": frequency_type,
                    "frequency": frequency,
                    "needExtendedHoursData": str(need_extended_hours_data).lower(),
                },
            )
            candles = data.get("candles", [])
            _log.info("[schwab] price history %s: %d candles", symbol, len(candles))
            # Normalize to standard format
            return [
                {
                    "timestamp": c.get("datetime", ""),  # epoch ms from Schwab
                    "open": float(c.get("open", 0)),
                    "high": float(c.get("high", 0)),
                    "low": float(c.get("low", 0)),
                    "close": float(c.get("close", 0)),
                    "volume": int(c.get("volume", 0)),
                }
                for c in candles
            ]
        except Exception as exc:
            _log.warning("[schwab] get_price_history failed for %s: %s — using fixture", symbol, exc)
            return self._fixture_price_history(symbol)

    # ── Fixtures (offline fallback) ────────────────────────────────────────────

    def _fixture_quote(self, symbol: str) -> dict[str, Any]:
        f = _FIXTURE_DIR / f"{symbol.lower()}_quote.json"
        if f.exists():
            return json.loads(f.read_text())
        _log.warning("[schwab] no quote fixture for %s", symbol)
        return {"symbol": symbol, "lastPrice": 0.0, "source": "fixture_missing"}

    def _fixture_options_chain(self, symbol: str) -> dict[str, Any]:
        f = _FIXTURE_DIR / f"{symbol.lower()}_chain.json"
        if f.exists():
            return json.loads(f.read_text())
        _log.warning("[schwab] no options chain fixture for %s", symbol)
        return {"symbol": symbol, "callExpDateMap": {}, "putExpDateMap": {}, "source": "fixture_missing"}

    def _fixture_price_history(self, symbol: str) -> list[dict[str, Any]]:
        f = _FIXTURE_DIR / f"{symbol.lower()}_history.json"
        if f.exists():
            return json.loads(f.read_text())
        _log.warning("[schwab] no price history fixture for %s", symbol)
        return []
