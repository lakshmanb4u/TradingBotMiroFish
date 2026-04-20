"""VIX service: fetch fear index from Alpha Vantage, derive regime signals."""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

import httpx

_ROOT = Path(__file__).resolve().parents[2]
_log = logging.getLogger(__name__)


def _vix_regime(vix: float) -> str:
    if vix < 15:
        return "low"
    elif vix < 25:
        return "normal"
    elif vix <= 35:
        return "elevated"
    return "extreme"


def _fear_signal(vix: float) -> str:
    if vix < 15:
        return "greed"
    elif vix < 20:
        return "neutral"
    elif vix <= 30:
        return "fear"
    return "extreme_fear"


def _vix_trend(closes: list[float]) -> str:
    if len(closes) < 3:
        return "flat"
    last3 = closes[-3:]
    if last3[-1] > last3[0] * 1.02:
        return "rising"
    elif last3[-1] < last3[0] * 0.98:
        return "falling"
    return "flat"


class VIXService:
    def __init__(self) -> None:
        self._api_key: str | None = os.environ.get("ALPHA_VANTAGE_API_KEY")

    def collect(self) -> dict[str, Any]:
        provider_mode = "alpha_vantage_live"
        raw_data: dict[str, Any] = {}

        if not self._api_key:
            raw_data, provider_mode = self._load_fixture()
        else:
            try:
                url = (
                    f"https://www.alphavantage.co/query"
                    f"?function=TIME_SERIES_DAILY&symbol=VIX"
                    f"&outputsize=compact&apikey={self._api_key}"
                )
                resp = httpx.get(url, timeout=15)
                resp.raise_for_status()
                raw_data = resp.json()
            except Exception as exc:
                _log.warning("VIX fetch failed: %s", exc)
                raw_data, provider_mode = self._load_fixture()

        # Parse time series
        ts = raw_data.get("Time Series (Daily)", {})
        if not ts:
            ts = raw_data.get("series", {})

        closes: list[float] = []
        for day in sorted(ts.keys(), reverse=True)[:10]:
            entry = ts[day]
            try:
                closes.append(float(entry.get("4. close", entry.get("close", 0))))
            except (ValueError, TypeError):
                pass

        closes.reverse()

        vix_current = closes[-1] if closes else 20.0
        vix_5d_avg = round(sum(closes[-5:]) / max(len(closes[-5:]), 1), 2) if closes else 20.0

        # Persist raw
        today_str = date.today().strftime("%Y%m%d")
        raw_dir = _ROOT / "state" / "raw" / "vix"
        raw_dir.mkdir(parents=True, exist_ok=True)
        try:
            (raw_dir / f"VIX_{today_str}.json").write_text(
                json.dumps({"provider_mode": provider_mode, "raw": raw_data}, indent=2)
            )
        except Exception as exc:
            _log.warning("VIX persist failed: %s", exc)

        return {
            "vix_current": round(vix_current, 2),
            "vix_5d_avg": vix_5d_avg,
            "vix_regime": _vix_regime(vix_current),
            "vix_trend": _vix_trend(closes),
            "fear_signal": _fear_signal(vix_current),
            "provider_mode": provider_mode,
            "source_audit": {
                "vix": {
                    "status": "live" if provider_mode == "alpha_vantage_live" else "fallback",
                    "provider": "alphavantage" if provider_mode == "alpha_vantage_live" else "fixture",
                    "record_count": len(closes),
                }
            },
        }

    def _load_fixture(self) -> tuple[dict[str, Any], str]:
        fixture_path = _ROOT / "infra" / "fixtures" / "vix" / "VIX.json"
        if fixture_path.exists():
            try:
                return json.loads(fixture_path.read_text()), "fixture_fallback"
            except Exception:
                pass
        # Synthetic fallback
        return {
            "Time Series (Daily)": {
                "2024-01-05": {"4. close": "13.5"},
                "2024-01-04": {"4. close": "13.8"},
                "2024-01-03": {"4. close": "14.1"},
                "2024-01-02": {"4. close": "13.9"},
                "2024-01-01": {"4. close": "13.2"},
            }
        }, "fixture_fallback"
