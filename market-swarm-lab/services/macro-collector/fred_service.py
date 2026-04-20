"""FRED service: fetch macro indicators from Federal Reserve public JSON API."""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import httpx

_ROOT = Path(__file__).resolve().parents[2]
_log = logging.getLogger(__name__)

_FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.json"

_SERIES = {
    "DFF": "fed_funds_rate",
    "T10Y2Y": "yield_curve",
    "BAMLH0A0HYM2": "credit_spread",
    "UMCSENT": "consumer_sentiment",
}

_FALLBACK_VALUES = {
    "fed_funds_rate": 5.33,
    "yield_curve": 0.1,
    "credit_spread": 3.5,
    "consumer_sentiment": 68.0,
}


def _latest_value(data: list) -> float | None:
    """Return last non-null value from FRED [[date, value], ...] list."""
    for _, val in reversed(data):
        try:
            v = float(val)
            if v == v:  # NaN check
                return v
        except (TypeError, ValueError):
            pass
    return None


def _yield_curve_signal(yc: float) -> str:
    if yc < 0:
        return "inverted"
    elif yc <= 0.5:
        return "flat"
    return "normal"


def _credit_signal(spread: float) -> str:
    if spread < 3.0:
        return "tight"
    elif spread <= 5.0:
        return "normal"
    return "wide"


def _macro_regime(yield_curve: float, credit_spread: float) -> str:
    if yield_curve < -0.2 or credit_spread > 5.0:
        return "risk_off"
    elif yield_curve > 0.5 and credit_spread < 3.5:
        return "risk_on"
    return "neutral"


class FREDService:
    def collect(self) -> dict[str, Any]:
        values: dict[str, float] = {}
        fetched = 0
        errors = 0

        for series_id, field_name in _SERIES.items():
            try:
                resp = httpx.get(_FRED_BASE, params={"id": series_id}, timeout=15)
                resp.raise_for_status()
                payload = resp.json()
                # FRED returns {"observations": [[date, value], ...]}
                obs = payload if isinstance(payload, list) else payload.get("observations", payload)
                val = _latest_value(obs) if isinstance(obs, list) else None
                if val is not None:
                    values[field_name] = val
                    fetched += 1
                else:
                    values[field_name] = _FALLBACK_VALUES[field_name]
                    errors += 1
            except Exception as exc:
                _log.warning("FRED %s fetch failed: %s", series_id, exc)
                values[field_name] = _FALLBACK_VALUES[field_name]
                errors += 1

        provider_mode = "fred_live" if fetched > 0 else "fixture_fallback"

        fed = values.get("fed_funds_rate", _FALLBACK_VALUES["fed_funds_rate"])
        yc = values.get("yield_curve", _FALLBACK_VALUES["yield_curve"])
        spread = values.get("credit_spread", _FALLBACK_VALUES["credit_spread"])
        sentiment = values.get("consumer_sentiment", _FALLBACK_VALUES["consumer_sentiment"])

        # Persist raw
        today_str = date.today().strftime("%Y%m%d")
        raw_dir = _ROOT / "state" / "raw" / "macro"
        raw_dir.mkdir(parents=True, exist_ok=True)
        try:
            (raw_dir / f"fred_{today_str}.json").write_text(
                json.dumps({"provider_mode": provider_mode, "values": values}, indent=2)
            )
        except Exception as exc:
            _log.warning("FRED persist failed: %s", exc)

        return {
            "fed_funds_rate": round(fed, 4),
            "yield_curve": round(yc, 4),
            "yield_curve_signal": _yield_curve_signal(yc),
            "credit_spread": round(spread, 4),
            "credit_signal": _credit_signal(spread),
            "consumer_sentiment": round(sentiment, 2),
            "macro_regime": _macro_regime(yc, spread),
            "provider_mode": provider_mode,
            "source_audit": {
                "macro": {
                    "status": "live" if provider_mode == "fred_live" else "fallback",
                    "provider": "fred" if provider_mode == "fred_live" else "fixture",
                    "record_count": fetched,
                }
            },
        }
