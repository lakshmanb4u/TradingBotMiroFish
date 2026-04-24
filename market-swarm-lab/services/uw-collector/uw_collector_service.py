"""Unusual Whales Collector Service.

Fetches live options flow, dark pool, and GEX data for intraday signal generation.

Key endpoints used:
  - /api/stock/{ticker}/flow-alerts       — unusual sweeps
  - /api/stock/{ticker}/flow-recent       — recent options flow
  - /api/darkpool/{ticker}                — dark pool prints
  - /api/stock/{ticker}/greek-exposure    — GEX (gamma exposure)
  - /api/stock/{ticker}/greek-exposure/strike — GEX by strike (key levels)
  - /api/option-trades/flow-alerts        — market-wide flow alerts

Output bundle:
{
    "symbol": str,
    "source": "uw_live" | "uw_error",
    "flow_alerts": [...],      # unusual sweeps
    "flow_recent": [...],      # recent flow
    "darkpool": [...],         # dark pool prints
    "gex": {...},              # overall GEX
    "gex_by_strike": [...],    # GEX flip levels
    "flow_bias": "bullish"|"bearish"|"neutral",
    "gex_flip_level": float,   # key support/resistance from GEX
    "signals": [...]           # derived intraday signals
}
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass

_log = logging.getLogger(__name__)

UW_BASE = "https://api.unusualwhales.com"
UW_KEY = os.environ.get("UW_API_KEY", "")


class UWCollectorService:

    def __init__(self) -> None:
        self._session = requests.Session()
        if not UW_KEY:
            _log.warning("[uw] UW_API_KEY not set")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {UW_KEY}",
            "Accept": "application/json",
        }

    def _get(self, path: str, params: dict | None = None) -> Any:
        url = f"{UW_BASE}{path}"
        resp = self._session.get(url, headers=self._headers(), params=params or {}, timeout=15)
        resp.raise_for_status()
        return resp.json()

    # ── Individual Fetchers ────────────────────────────────────────────────────

    def fetch_flow_alerts(self, symbol: str, limit: int = 20) -> list[dict]:
        try:
            data = self._get(f"/api/stock/{symbol}/flow-alerts", {"limit": limit})
            alerts = data.get("data", data) if isinstance(data, dict) else data
            _log.info("[uw] flow_alerts %s: %d", symbol, len(alerts))
            return alerts[:limit] if isinstance(alerts, list) else []
        except Exception as e:
            _log.warning("[uw] flow_alerts failed: %s", e)
            return []

    def fetch_flow_recent(self, symbol: str, limit: int = 20) -> list[dict]:
        try:
            data = self._get(f"/api/stock/{symbol}/flow-recent", {"limit": limit})
            flows = data.get("data", data) if isinstance(data, dict) else data
            _log.info("[uw] flow_recent %s: %d", symbol, len(flows))
            return flows[:limit] if isinstance(flows, list) else []
        except Exception as e:
            _log.warning("[uw] flow_recent failed: %s", e)
            return []

    def fetch_darkpool(self, symbol: str, limit: int = 10) -> list[dict]:
        try:
            data = self._get(f"/api/darkpool/{symbol}", {"limit": limit})
            prints = data.get("data", data) if isinstance(data, dict) else data
            _log.info("[uw] darkpool %s: %d", symbol, len(prints))
            return prints[:limit] if isinstance(prints, list) else []
        except Exception as e:
            _log.warning("[uw] darkpool failed: %s", e)
            return []

    def fetch_gex(self, symbol: str) -> dict:
        try:
            data = self._get(f"/api/stock/{symbol}/greek-exposure")
            _log.info("[uw] gex %s: ok", symbol)
            return data.get("data", data) if isinstance(data, dict) else {}
        except Exception as e:
            _log.warning("[uw] gex failed: %s", e)
            return {}

    def fetch_gex_by_strike(self, symbol: str) -> list[dict]:
        try:
            data = self._get(f"/api/stock/{symbol}/greek-exposure/strike")
            strikes = data.get("data", data) if isinstance(data, dict) else data
            _log.info("[uw] gex_by_strike %s: %d strikes", symbol, len(strikes) if isinstance(strikes, list) else 0)
            return strikes if isinstance(strikes, list) else []
        except Exception as e:
            _log.warning("[uw] gex_by_strike failed: %s", e)
            return []

    # ── Signal Derivation ──────────────────────────────────────────────────────

    def _derive_flow_bias(self, flow_alerts: list[dict], flow_recent: list[dict]) -> str:
        """Compute bullish/bearish bias from options flow."""
        bullish = 0
        bearish = 0

        for item in flow_alerts + flow_recent:
            side = str(item.get("put_call", item.get("type", ""))).upper()
            sentiment = str(item.get("sentiment", "")).lower()
            premium = float(item.get("premium", item.get("total_premium", 0)) or 0)

            if side == "CALL" or sentiment == "bullish":
                bullish += max(1, int(premium / 10000))
            elif side == "PUT" or sentiment == "bearish":
                bearish += max(1, int(premium / 10000))

        total = bullish + bearish
        if total == 0:
            return "neutral"
        ratio = bullish / total
        if ratio > 0.55:
            return "bullish"
        elif ratio < 0.45:
            return "bearish"
        return "neutral"

    def _find_gex_flip_level(self, gex_by_strike: list[dict]) -> float | None:
        """Find the GEX flip level (strike where GEX crosses zero = key support/resistance)."""
        if not gex_by_strike:
            return None

        # Sort by strike
        try:
            sorted_strikes = sorted(
                gex_by_strike,
                key=lambda x: float(x.get("strike", x.get("price", 0)) or 0)
            )
        except Exception:
            return None

        # Find zero crossing
        prev_gex = None
        for s in sorted_strikes:
            gex_val = float(s.get("gamma_exposure", s.get("gex", s.get("value", 0))) or 0)
            strike = float(s.get("strike", s.get("price", 0)) or 0)
            if prev_gex is not None and prev_gex * gex_val < 0:
                return round(strike, 2)
            prev_gex = gex_val
        return None

    def _derive_signals(
        self,
        flow_alerts: list[dict],
        darkpool: list[dict],
        gex_flip: float | None,
        flow_bias: str,
        current_price: float = 0.0,
    ) -> list[dict]:
        signals = []

        # Large put sweep → put signal
        for alert in flow_alerts:
            side = str(alert.get("put_call", alert.get("type", ""))).upper()
            premium = float(alert.get("premium", alert.get("total_premium", 0)) or 0)
            if side == "PUT" and premium > 500_000:
                signals.append({
                    "type": "UNUSUAL_PUT_SWEEP",
                    "action": "BUY_PUTS",
                    "premium": premium,
                    "strike": alert.get("strike"),
                    "expiry": alert.get("expiry", alert.get("expiration_date", "")),
                    "reason": f"Large put sweep ${premium/1e6:.1f}M premium",
                    "confidence": min(0.85, 0.6 + premium / 5_000_000),
                })

        # Large call sweep → call signal
        for alert in flow_alerts:
            side = str(alert.get("put_call", alert.get("type", ""))).upper()
            premium = float(alert.get("premium", alert.get("total_premium", 0)) or 0)
            if side == "CALL" and premium > 500_000:
                signals.append({
                    "type": "UNUSUAL_CALL_SWEEP",
                    "action": "BUY_CALLS",
                    "premium": premium,
                    "strike": alert.get("strike"),
                    "expiry": alert.get("expiry", alert.get("expiration_date", "")),
                    "reason": f"Large call sweep ${premium/1e6:.1f}M premium",
                    "confidence": min(0.85, 0.6 + premium / 5_000_000),
                })

        # GEX flip level proximity
        if gex_flip and current_price:
            dist = abs(current_price - gex_flip) / current_price
            if dist < 0.005:  # within 0.5% of flip level
                signals.append({
                    "type": "GEX_FLIP_PROXIMITY",
                    "action": "WATCH",
                    "gex_flip_level": gex_flip,
                    "current_price": current_price,
                    "distance_pct": round(dist * 100, 3),
                    "reason": f"Price ${current_price:.2f} near GEX flip ${gex_flip:.2f} — key level",
                    "confidence": 0.70,
                })

        # Dark pool large prints
        for dp in darkpool:
            size = float(dp.get("size", dp.get("volume", dp.get("quantity", 0))) or 0)
            price = float(dp.get("price", dp.get("executed_price", 0)) or 0)
            notional = size * price
            if notional > 10_000_000:  # $10M+
                signals.append({
                    "type": "DARK_POOL_PRINT",
                    "action": "WATCH",
                    "notional": round(notional),
                    "price": price,
                    "size": size,
                    "reason": f"Dark pool ${notional/1e6:.1f}M print at ${price:.2f}",
                    "confidence": 0.65,
                })

        return signals

    # ── Main Collect ───────────────────────────────────────────────────────────

    def collect(self, symbol: str, current_price: float = 0.0) -> dict[str, Any]:
        symbol = symbol.upper()

        flow_alerts = self.fetch_flow_alerts(symbol)
        flow_recent = self.fetch_flow_recent(symbol)
        darkpool = self.fetch_darkpool(symbol)
        gex = self.fetch_gex(symbol)
        gex_by_strike = self.fetch_gex_by_strike(symbol)

        flow_bias = self._derive_flow_bias(flow_alerts, flow_recent)
        gex_flip = self._find_gex_flip_level(gex_by_strike)
        signals = self._derive_signals(flow_alerts, darkpool, gex_flip, flow_bias, current_price)

        source = "uw_live" if (flow_alerts or flow_recent or gex) else "uw_empty"

        return {
            "symbol": symbol,
            "source": source,
            "flow_alerts": flow_alerts[:10],
            "flow_recent": flow_recent[:10],
            "darkpool": darkpool[:5],
            "gex": gex,
            "gex_by_strike": gex_by_strike[:20],
            "flow_bias": flow_bias,
            "gex_flip_level": gex_flip,
            "signals": signals,
            "summary": {
                "flow_alerts_count": len(flow_alerts),
                "darkpool_count": len(darkpool),
                "gex_strikes": len(gex_by_strike),
                "flow_bias": flow_bias,
                "gex_flip_level": gex_flip,
                "signal_count": len(signals),
            },
        }


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)

    svc = UWCollectorService()
    result = svc.collect("SPY")

    print(f"\n{'='*50}")
    print(f"Unusual Whales — SPY")
    print(f"{'='*50}")
    print(f"Source:        {result['source']}")
    print(f"Flow bias:     {result['flow_bias']}")
    print(f"GEX flip:      {result['gex_flip_level']}")
    print(f"Flow alerts:   {result['summary']['flow_alerts_count']}")
    print(f"Dark pool:     {result['summary']['darkpool_count']}")

    print(f"\nSIGNALS ({len(result['signals'])}):")
    for s in result["signals"]:
        print(f"  {s['action']:12s} | {s['type']:25s} | {s['reason']}")

    if not result["signals"]:
        print("  No signals.")

    print(f"\nRaw flow sample:")
    print(json.dumps(result["flow_alerts"][:2], indent=2))
