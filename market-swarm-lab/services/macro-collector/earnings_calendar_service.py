"""Earnings calendar service: upcoming earnings from Financial Modeling Prep free tier."""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

_ROOT = Path(__file__).resolve().parents[2]
_log = logging.getLogger(__name__)

_BASE = "https://financialmodelingprep.com/api/v3/earning_calendar"


class EarningsCalendarService:
    def collect(self, ticker: str) -> dict[str, Any]:
        ticker = ticker.upper()
        provider_mode = "fmp_live"
        calendar: list[dict[str, Any]] = []

        today = date.today()
        to_date = today + timedelta(days=7)

        try:
            resp = httpx.get(
                _BASE,
                params={"from": today.isoformat(), "to": to_date.isoformat()},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                calendar = data
            elif isinstance(data, dict):
                calendar = data.get("earningsCalendar", [])
        except Exception as exc:
            _log.warning("Earnings calendar fetch failed for %s: %s", ticker, exc)
            calendar, provider_mode = self._load_fixture(ticker)

        # Find entry for this ticker
        match: dict[str, Any] | None = None
        for entry in calendar:
            if entry.get("symbol", "").upper() == ticker:
                match = entry
                break

        has_earnings_soon = match is not None
        earnings_date: str | None = None
        days_until: int | None = None
        eps_estimate: float | None = None
        revenue_estimate: float | None = None
        earnings_risk = "none"

        if match:
            earnings_date = match.get("date")
            try:
                ed = date.fromisoformat(str(earnings_date))
                days_until = (ed - today).days
            except (ValueError, TypeError):
                days_until = None

            try:
                eps_estimate = float(match.get("epsEstimated") or match.get("eps_estimate") or 0)
            except (ValueError, TypeError):
                eps_estimate = None

            try:
                revenue_estimate = float(match.get("revenueEstimated") or match.get("revenue_estimate") or 0)
            except (ValueError, TypeError):
                revenue_estimate = None

            if days_until is not None:
                if days_until <= 2:
                    earnings_risk = "high"
                elif days_until <= 5:
                    earnings_risk = "medium"
                else:
                    earnings_risk = "low"

        # Persist raw
        today_str = today.strftime("%Y%m%d")
        raw_dir = _ROOT / "state" / "raw" / "earnings"
        raw_dir.mkdir(parents=True, exist_ok=True)
        try:
            (raw_dir / f"{ticker}_{today_str}.json").write_text(
                json.dumps({"ticker": ticker, "provider_mode": provider_mode, "calendar": calendar}, indent=2)
            )
        except Exception as exc:
            _log.warning("Earnings persist failed: %s", exc)

        return {
            "ticker": ticker,
            "has_earnings_soon": has_earnings_soon,
            "earnings_date": earnings_date,
            "days_until_earnings": days_until,
            "eps_estimate": eps_estimate,
            "revenue_estimate": revenue_estimate,
            "earnings_risk": earnings_risk,
            "provider_mode": provider_mode,
            "source_audit": {
                "earnings": {
                    "status": "live" if provider_mode == "fmp_live" else "fallback",
                    "provider": "fmp" if provider_mode == "fmp_live" else "fixture",
                    "record_count": len(calendar),
                }
            },
        }

    def _load_fixture(self, ticker: str) -> tuple[list[dict[str, Any]], str]:
        fixture_path = _ROOT / "infra" / "fixtures" / "earnings" / f"{ticker}.json"
        if fixture_path.exists():
            try:
                data = json.loads(fixture_path.read_text())
                return data.get("calendar", []), "fixture_fallback"
            except Exception:
                pass
        return [], "fixture_fallback"
