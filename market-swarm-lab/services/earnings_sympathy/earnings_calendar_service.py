"""EarningsCalendarService — loads upcoming and past earnings from config/earnings_calendar.json.

No live API required. Supports manual JSON config at config/earnings_calendar.json.
Format: [{"ticker": "INTC", "date": "2026-04-30", "time": "after_close", "sector": "semiconductors"}]
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _ROOT / "config" / "earnings_calendar.json"


@dataclass
class EarningsEvent:
    ticker: str
    date: str          # YYYY-MM-DD
    time: str          # "before_open" | "after_close"
    sector: str = ""
    industry: str = ""
    expected_move_pct: Optional[float] = None
    implied_volatility: Optional[float] = None

    def days_until(self) -> int:
        return (datetime.strptime(self.date, "%Y-%m-%d").date() - date.today()).days

    def is_past(self) -> bool:
        return self.days_until() < 0

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "date": self.date,
            "time": self.time,
            "sector": self.sector,
            "industry": self.industry,
            "days_until": self.days_until(),
            "expected_move_pct": self.expected_move_pct,
            "implied_volatility": self.implied_volatility,
        }


class EarningsCalendarService:
    """Load earnings events from config/earnings_calendar.json."""

    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path = config_path or _CONFIG_PATH

    def _load_raw(self) -> list[dict]:
        if not self._config_path.exists():
            _log.warning("[earnings_calendar] no config at %s — returning empty", self._config_path)
            return []
        try:
            return json.loads(self._config_path.read_text())
        except Exception as exc:
            _log.error("[earnings_calendar] load error: %s", exc)
            return []

    def fetch_upcoming(self, days_ahead: int = 14) -> list[EarningsEvent]:
        """Return upcoming earnings in the next `days_ahead` days (today inclusive)."""
        today = date.today()
        cutoff = today + timedelta(days=days_ahead)
        events: list[EarningsEvent] = []
        for item in self._load_raw():
            try:
                d = datetime.strptime(item["date"], "%Y-%m-%d").date()
                if today <= d <= cutoff:
                    events.append(self._make(item))
            except Exception as exc:
                _log.debug("[earnings_calendar] skip %s: %s", item, exc)
        events.sort(key=lambda e: e.date)
        _log.info("[earnings_calendar] %d upcoming events in %d days", len(events), days_ahead)
        return events

    def fetch_all(self) -> list[EarningsEvent]:
        events: list[EarningsEvent] = []
        for item in self._load_raw():
            try:
                events.append(self._make(item))
            except Exception:
                pass
        return events

    def has_earnings_soon(self, ticker: str, within_days: int = 5) -> bool:
        """True if ticker has an earnings event within N days."""
        ticker = ticker.upper()
        return any(e.ticker == ticker for e in self.fetch_upcoming(days_ahead=within_days))

    def get_past_earnings(self, ticker: str, lookback_days: int = 400) -> list[EarningsEvent]:
        """Return past earnings for a ticker (for historical sympathy analysis)."""
        ticker = ticker.upper()
        today = date.today()
        floor = today - timedelta(days=lookback_days)
        events = []
        for item in self._load_raw():
            if item.get("ticker", "").upper() != ticker:
                continue
            try:
                d = datetime.strptime(item["date"], "%Y-%m-%d").date()
                if floor <= d < today:
                    events.append(self._make(item))
            except Exception:
                pass
        events.sort(key=lambda e: e.date, reverse=True)
        return events

    @staticmethod
    def _make(item: dict) -> EarningsEvent:
        return EarningsEvent(
            ticker=item["ticker"].upper(),
            date=item["date"],
            time=item.get("time", "after_close"),
            sector=item.get("sector", ""),
            industry=item.get("industry", ""),
            expected_move_pct=item.get("expected_move_pct"),
            implied_volatility=item.get("implied_volatility"),
        )
