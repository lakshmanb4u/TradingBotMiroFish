"""UWHistoricalFetcher — fetch Unusual Whales point-in-time data for a past date.

CONFIRMED WORKING endpoints (live-probed 2026-04-25):
  ✅ /api/stock/{ticker}/flow-per-strike?date=   — call/put vol+premium by strike
  ✅ /api/stock/{ticker}/oi-per-strike?date=     — OI by strike
  ✅ /api/stock/{ticker}/oi-per-expiry?date=     — OI by expiry
  ✅ /api/stock/{ticker}/oi-change?date=         — top OI movers (new positioning)
  ✅ /api/stock/{ticker}/ohlc/1d?date=           — OHLCV bars around date
  ✅ /api/stock/{ticker}/net-prem-ticks?date=    — intraday net premium ticks
  ✅ /api/stock/{ticker}/iv-rank?timespan=1m     — last 30 days IV rank history

NOT WORKING for historical (silently return current data or empty):
  ❌ /api/stock/{ticker}/flow-alerts?date=       — ignores date param
  ❌ /api/stock/{ticker}/flow-recent             — live only
  ❌ /api/stock/{ticker}/options-volume?date=    — ignores date param
  ❌ /api/darkpool/{ticker}?date=                — returns empty for past dates
  ❌ /api/option-trades/full-tape/{date}         — requires $250/mo tier

Within 30-day window only (Basic $150/mo plan).
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parents[2]
_SNAP_DIR = _ROOT / "state" / "backtests" / "uw_snapshots"

UW_BASE = "https://api.unusualwhales.com"


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _load_env() -> str:
    env_path = _ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    return os.environ.get("UW_API_KEY", "")


class UWHistoricalFetcher:
    """
    Fetch UW data for a specific past trading date using confirmed working endpoints.
    All responses are filtered to the as-of date. Clearly audits each source.
    """

    def __init__(self, as_of: str) -> None:
        self.as_of = as_of
        self.as_of_date = datetime.strptime(as_of, "%Y-%m-%d").date()
        self._key = _load_env()
        self._available = bool(self._key)
        _ensure_dir(_SNAP_DIR)

    def fetch_all(self, ticker: str) -> dict[str, Any]:
        ticker = ticker.upper()
        _log.info("[uw_hist] fetching %s as-of %s", ticker, self.as_of)

        if not self._available:
            return {"ticker": ticker, "as_of": self.as_of,
                    "error": "UW_API_KEY not set", "overall_audit": {"quality": "poor"}}

        endpoints: dict[str, Any] = {}

        # ── 1. Flow per strike (confirmed working) ────────────────────────────
        data, audit = self._fetch_flow_per_strike(ticker)
        endpoints["flow_per_strike"] = {"data": data, "audit": audit}

        # ── 2. OI per strike (confirmed working) ──────────────────────────────
        data, audit = self._fetch_oi_per_strike(ticker)
        endpoints["oi_per_strike"] = {"data": data, "audit": audit}

        # ── 3. OI per expiry (confirmed working) ──────────────────────────────
        data, audit = self._fetch_oi_per_expiry(ticker)
        endpoints["oi_per_expiry"] = {"data": data, "audit": audit}

        # ── 4. OI change — top new positioning (confirmed working) ────────────
        data, audit = self._fetch_oi_change(ticker)
        endpoints["oi_change"] = {"data": data, "audit": audit}

        # ── 5. Net premium ticks — intraday call/put flow (confirmed working) ─
        data, audit = self._fetch_net_prem_ticks(ticker)
        endpoints["net_prem_ticks"] = {"data": data, "audit": audit}

        # ── 6. OHLCV bars (confirmed working) ────────────────────────────────
        data, audit = self._fetch_ohlc(ticker)
        endpoints["ohlc"] = {"data": data, "audit": audit}

        # ── 7. IV rank history (confirmed working up to 30 days) ─────────────
        data, audit = self._fetch_iv_rank(ticker)
        endpoints["iv_rank"] = {"data": data, "audit": audit}

        # ── Build positioning summary ─────────────────────────────────────────
        positioning = self._build_positioning_summary(ticker, endpoints)
        overall_audit = self._overall_audit(endpoints)

        result = {
            "ticker": ticker,
            "as_of": self.as_of,
            "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
            "endpoints": endpoints,
            "positioning_summary": positioning,
            "overall_audit": overall_audit,
        }

        self._save_snapshot(ticker, result)
        return result

    # ── Confirmed Working Fetchers ─────────────────────────────────────────────

    def _fetch_flow_per_strike(self, ticker: str) -> tuple[list[dict], dict]:
        raw, code = self._get(f"/api/stock/{ticker}/flow-per-strike", {"date": self.as_of})
        items = self._extract_list(raw)
        # Verify date field matches
        items = [x for x in items if str(x.get("date", "")).startswith(self.as_of)]
        pit_safe = bool(items)
        return items, {
            "endpoint": f"/api/stock/{ticker}/flow-per-strike",
            "http_status": code,
            "point_in_time_safe": pit_safe,
            "record_count": len(items),
            "note": f"{len(items)} strike-level flow records for {self.as_of}" if pit_safe
                    else f"No data returned for {self.as_of} (status {code})",
        }

    def _fetch_oi_per_strike(self, ticker: str) -> tuple[list[dict], dict]:
        raw, code = self._get(f"/api/stock/{ticker}/oi-per-strike", {"date": self.as_of})
        items = self._extract_list(raw)
        items = [x for x in items if str(x.get("date", "")).startswith(self.as_of)]
        pit_safe = bool(items)
        return items, {
            "endpoint": f"/api/stock/{ticker}/oi-per-strike",
            "http_status": code,
            "point_in_time_safe": pit_safe,
            "record_count": len(items),
            "note": f"{len(items)} OI-per-strike records for {self.as_of}" if pit_safe
                    else f"No data for {self.as_of}",
        }

    def _fetch_oi_per_expiry(self, ticker: str) -> tuple[list[dict], dict]:
        raw, code = self._get(f"/api/stock/{ticker}/oi-per-expiry", {"date": self.as_of})
        items = self._extract_list(raw)
        items = [x for x in items if str(x.get("date", "")).startswith(self.as_of)]
        pit_safe = bool(items)
        return items, {
            "endpoint": f"/api/stock/{ticker}/oi-per-expiry",
            "http_status": code,
            "point_in_time_safe": pit_safe,
            "record_count": len(items),
            "note": f"{len(items)} expiry OI buckets for {self.as_of}" if pit_safe
                    else f"No data for {self.as_of}",
        }

    def _fetch_oi_change(self, ticker: str, limit: int = 50) -> tuple[list[dict], dict]:
        raw, code = self._get(f"/api/stock/{ticker}/oi-change",
                              {"date": self.as_of, "limit": limit})
        items = self._extract_list(raw)
        items = [x for x in items if str(x.get("curr_date", "")).startswith(self.as_of)]
        pit_safe = bool(items)
        return items, {
            "endpoint": f"/api/stock/{ticker}/oi-change",
            "http_status": code,
            "point_in_time_safe": pit_safe,
            "record_count": len(items),
            "note": f"{len(items)} OI-change records (top new positioning) for {self.as_of}"
                    if pit_safe else f"No data for {self.as_of}",
        }

    def _fetch_net_prem_ticks(self, ticker: str) -> tuple[list[dict], dict]:
        raw, code = self._get(f"/api/stock/{ticker}/net-prem-ticks", {"date": self.as_of})
        items = self._extract_list(raw)
        items = [x for x in items if str(x.get("date", "")).startswith(self.as_of)]
        pit_safe = bool(items)
        return items, {
            "endpoint": f"/api/stock/{ticker}/net-prem-ticks",
            "http_status": code,
            "point_in_time_safe": pit_safe,
            "record_count": len(items),
            "note": f"{len(items)} intraday net-premium ticks for {self.as_of}" if pit_safe
                    else f"No data for {self.as_of}",
        }

    def _fetch_ohlc(self, ticker: str) -> tuple[list[dict], dict]:
        raw, code = self._get(f"/api/stock/{ticker}/ohlc/1d",
                              {"date": self.as_of, "limit": 5})
        items = self._extract_list(raw)
        # Filter to regular session bar on as-of date
        day_bars = [x for x in items
                    if str(x.get("date", "")).startswith(self.as_of)
                    and x.get("market_time") == "r"]
        pit_safe = bool(day_bars)
        close = float(day_bars[0].get("close", 0)) if day_bars else None
        return day_bars, {
            "endpoint": f"/api/stock/{ticker}/ohlc/1d",
            "http_status": code,
            "point_in_time_safe": pit_safe,
            "record_count": len(day_bars),
            "close_price": close,
            "note": f"Regular session close: ${close} on {self.as_of}" if pit_safe
                    else f"No regular session bar for {self.as_of}",
        }

    def _fetch_iv_rank(self, ticker: str) -> tuple[dict | None, dict]:
        raw, code = self._get(f"/api/stock/{ticker}/iv-rank",
                              {"timespan": "1m"})
        items = self._extract_list(raw)
        match = next((x for x in items if str(x.get("date", "")).startswith(self.as_of)), None)
        pit_safe = match is not None
        available = [x.get("date") for x in items]
        return match, {
            "endpoint": f"/api/stock/{ticker}/iv-rank",
            "http_status": code,
            "point_in_time_safe": pit_safe,
            "available_dates": available,
            "note": (f"IV rank for {self.as_of}: vol={match.get('volatility')} "
                     f"iv_rank_1y={match.get('iv_rank_1y')} close={match.get('close')}")
                    if pit_safe else
                    f"No IV rank for {self.as_of}. Available: {available[-5:]}",
        }

    # ── Positioning Summary ────────────────────────────────────────────────────

    def _build_positioning_summary(self, ticker: str, endpoints: dict) -> dict[str, Any]:
        flow_strikes  = endpoints["flow_per_strike"]["data"]
        oi_strikes    = endpoints["oi_per_strike"]["data"]
        oi_expiries   = endpoints["oi_per_expiry"]["data"]
        oi_changes    = endpoints["oi_change"]["data"]
        prem_ticks    = endpoints["net_prem_ticks"]["data"]
        ohlc_bars     = endpoints["ohlc"]["data"]
        iv_rank       = endpoints["iv_rank"]["data"]

        close = float(ohlc_bars[0].get("close", 0)) if ohlc_bars else 0.0

        # ── Aggregate flow-per-strike ──────────────────────────────────────────
        total_call_vol = sum(int(x.get("call_volume", 0) or 0) for x in flow_strikes)
        total_put_vol  = sum(int(x.get("put_volume", 0) or 0) for x in flow_strikes)
        total_call_prem = sum(float(x.get("call_premium", 0) or 0) for x in flow_strikes)
        total_put_prem  = sum(float(x.get("put_premium", 0) or 0) for x in flow_strikes)
        total_call_otm_prem = sum(float(x.get("call_otm_premium", 0) or 0) for x in flow_strikes)
        total_put_otm_prem  = sum(float(x.get("put_otm_premium", 0) or 0) for x in flow_strikes)
        call_ask_vol = sum(int(x.get("call_volume_ask_side", 0) or 0) for x in flow_strikes)
        put_ask_vol  = sum(int(x.get("put_volume_ask_side", 0) or 0) for x in flow_strikes)

        cp_ratio = round(total_call_vol / total_put_vol, 3) if total_put_vol > 0 else 10.0

        # Top 5 strikes by call volume
        top_call_strikes = sorted(flow_strikes, key=lambda x: int(x.get("call_volume", 0) or 0), reverse=True)[:5]
        top_put_strikes  = sorted(flow_strikes, key=lambda x: int(x.get("put_volume", 0) or 0), reverse=True)[:5]

        # Top OTM call strikes (call_otm_volume > 0)
        otm_call_strikes = [x for x in flow_strikes if int(x.get("call_otm_volume", 0) or 0) > 500]
        otm_call_strikes.sort(key=lambda x: float(x.get("call_otm_premium", 0) or 0), reverse=True)

        # ── OI totals ─────────────────────────────────────────────────────────
        total_call_oi = sum(int(x.get("call_oi", 0) or 0) for x in oi_strikes)
        total_put_oi  = sum(int(x.get("put_oi", 0) or 0) for x in oi_strikes)

        # Near-term OI (3-14 DTE)
        near_term = [x for x in oi_expiries
                     if 3 <= (datetime.strptime(x.get("expiry","9999-01-01"), "%Y-%m-%d").date()
                               - self.as_of_date).days <= 14]
        near_call_oi = sum(int(x.get("call_oi", 0) or 0) for x in near_term)
        near_put_oi  = sum(int(x.get("put_oi", 0) or 0) for x in near_term)

        # ── OI change — top new positioning ───────────────────────────────────
        top_call_oi_change = [x for x in oi_changes
                              if "C" in x.get("option_symbol", "")][:5]
        top_put_oi_change  = [x for x in oi_changes
                              if "P" in x.get("option_symbol", "")][:5]

        # Repeated strike activity: strike appearing in top OI change with large vol
        repeated_call_strikes = [
            x for x in oi_changes
            if "C" in x.get("option_symbol", "")
            and int(x.get("volume", 0) or 0) > 2000
        ]

        # ── Net premium ticks — cumulative call vs put flow ───────────────────
        if prem_ticks:
            last_tick = prem_ticks[-1]
            cum_call_vol = int(last_tick.get("call_volume", 0) or 0)
            cum_put_vol  = int(last_tick.get("put_volume", 0) or 0)
            cum_net_call_prem = float(last_tick.get("net_call_premium", 0) or 0)
            cum_net_put_prem  = float(last_tick.get("net_put_premium", 0) or 0)
            # Ask-side dominance in call flow = buyers, not sellers
            call_ask_dominance = (
                sum(int(t.get("call_volume_ask_side", 0) or 0) for t in prem_ticks) /
                max(sum(int(t.get("call_volume", 0) or 0) for t in prem_ticks), 1)
            )
        else:
            cum_call_vol = cum_put_vol = 0
            cum_net_call_prem = cum_net_put_prem = 0.0
            call_ask_dominance = 0.5

        # ── IV rank ───────────────────────────────────────────────────────────
        iv_pct  = float(iv_rank.get("volatility", 0) or 0) * 100 if iv_rank else None
        iv_rank_1y = float(iv_rank.get("iv_rank_1y", 0) or 0) if iv_rank else None

        # ── Pre-positioning detection ─────────────────────────────────────────
        flags = self._detect_pre_positioning(
            cp_ratio=cp_ratio,
            call_ask_vol=call_ask_vol,
            put_ask_vol=put_ask_vol,
            total_call_vol=total_call_vol,
            total_put_vol=total_put_vol,
            total_call_otm_prem=total_call_otm_prem,
            total_put_otm_prem=total_put_otm_prem,
            repeated_call_strikes=repeated_call_strikes,
            otm_call_strikes=otm_call_strikes,
            cum_net_call_prem=cum_net_call_prem,
            iv_rank_1y=iv_rank_1y,
            call_ask_dominance=call_ask_dominance,
        )

        return {
            "ticker": ticker,
            "as_of": self.as_of,
            "close_price": close,
            "iv_volatility_pct": round(iv_pct, 1) if iv_pct else None,
            "iv_rank_1y": round(iv_rank_1y, 1) if iv_rank_1y else None,
            "flow_summary": {
                "total_call_volume": total_call_vol,
                "total_put_volume": total_put_vol,
                "call_put_ratio": cp_ratio,
                "call_ask_side_volume": call_ask_vol,
                "put_ask_side_volume": put_ask_vol,
                "call_ask_dominance_pct": round(call_ask_dominance * 100, 1),
                "total_call_premium": round(total_call_prem),
                "total_put_premium": round(total_put_prem),
                "total_call_otm_premium": round(total_call_otm_prem),
                "total_put_otm_premium": round(total_put_otm_prem),
                "cum_net_call_premium": round(cum_net_call_prem),
                "cum_net_put_premium": round(cum_net_put_prem),
            },
            "oi_summary": {
                "total_call_oi": total_call_oi,
                "total_put_oi": total_put_oi,
                "near_term_call_oi": near_call_oi,
                "near_term_put_oi": near_put_oi,
            },
            "top_call_strikes": [
                {"strike": x.get("strike"), "call_volume": x.get("call_volume"),
                 "call_otm_premium": x.get("call_otm_premium"),
                 "call_ask_side": x.get("call_volume_ask_side")}
                for x in top_call_strikes
            ],
            "top_put_strikes": [
                {"strike": x.get("strike"), "put_volume": x.get("put_volume"),
                 "put_otm_premium": x.get("put_otm_premium")}
                for x in top_put_strikes
            ],
            "otm_call_strikes": [
                {"strike": x.get("strike"), "call_otm_volume": x.get("call_otm_volume"),
                 "call_otm_premium": x.get("call_otm_premium")}
                for x in otm_call_strikes[:5]
            ],
            "top_call_oi_change": [
                {"symbol": x.get("option_symbol"), "volume": x.get("volume"),
                 "curr_oi": x.get("curr_oi"), "oi_change": x.get("oi_change"),
                 "avg_price": x.get("avg_price")}
                for x in top_call_oi_change
            ],
            "repeated_call_strikes": [
                {"symbol": x.get("option_symbol"), "volume": x.get("volume"),
                 "curr_oi": x.get("curr_oi")}
                for x in repeated_call_strikes[:5]
            ],
            "pre_positioning": flags,
        }

    def _detect_pre_positioning(
        self,
        cp_ratio: float,
        call_ask_vol: int,
        put_ask_vol: int,
        total_call_vol: int,
        total_put_vol: int,
        total_call_otm_prem: float,
        total_put_otm_prem: float,
        repeated_call_strikes: list,
        otm_call_strikes: list,
        cum_net_call_prem: float,
        iv_rank_1y: float | None,
        call_ask_dominance: float,
    ) -> dict[str, Any]:
        signals: list[str] = []
        bull_score = 0
        bear_score = 0

        if cp_ratio > 1.3:
            bull_score += 2
            signals.append(f"✅ call/put ratio {cp_ratio:.2f} > 1.3 (bullish)")
        elif cp_ratio < 0.7:
            bear_score += 2
            signals.append(f"🔴 call/put ratio {cp_ratio:.2f} < 0.7 (bearish)")

        if call_ask_vol > put_ask_vol * 1.5:
            bull_score += 2
            signals.append(f"✅ call ask-side volume {call_ask_vol:,} >> put ask-side {put_ask_vol:,} (buyers in calls)")
        elif put_ask_vol > call_ask_vol * 1.5:
            bear_score += 2
            signals.append(f"🔴 put ask-side volume {put_ask_vol:,} >> call ask-side {call_ask_vol:,} (buyers in puts)")

        if call_ask_dominance > 0.55:
            bull_score += 1
            signals.append(f"✅ {call_ask_dominance*100:.0f}% of call vol was ask-side (buyers, not sellers)")

        if total_call_otm_prem > total_put_otm_prem * 1.5:
            bull_score += 2
            signals.append(f"✅ OTM call premium ${total_call_otm_prem/1e6:.1f}M >> OTM put ${total_put_otm_prem/1e6:.1f}M")
        elif total_put_otm_prem > total_call_otm_prem * 1.5:
            bear_score += 2
            signals.append(f"🔴 OTM put premium ${total_put_otm_prem/1e6:.1f}M >> OTM call ${total_call_otm_prem/1e6:.1f}M")

        if len(repeated_call_strikes) >= 2:
            bull_score += 2
            signals.append(f"✅ {len(repeated_call_strikes)} call strikes with >2K volume each (repeated positioning)")

        if len(otm_call_strikes) >= 3:
            bull_score += 1
            signals.append(f"✅ {len(otm_call_strikes)} OTM call strikes with significant premium (pre-positioning)")

        if cum_net_call_prem > 0:
            bull_score += 1
            signals.append(f"✅ net call premium positive ${cum_net_call_prem/1e6:.1f}M (cumulative bullish flow)")
        elif cum_net_call_prem < 0:
            bear_score += 1
            signals.append(f"🔴 net call premium negative (cumulative bearish flow)")

        if iv_rank_1y and iv_rank_1y > 80:
            signals.append(f"⚠️ IV rank {iv_rank_1y:.0f} — elevated, options are expensive")
        elif iv_rank_1y and iv_rank_1y < 40:
            bull_score += 1
            signals.append(f"✅ IV rank {iv_rank_1y:.0f} — cheap options, good for OTM buys")

        bullish = bull_score >= 3
        bearish = bear_score >= 3
        confidence = "high" if (bull_score >= 5 or bear_score >= 5) else (
            "medium" if (bull_score >= 3 or bear_score >= 3) else "low"
        )

        return {
            "bullish_call_positioning": bullish,
            "bearish_put_positioning": bearish,
            "bull_score": bull_score,
            "bear_score": bear_score,
            "confidence": confidence,
            "signals": signals,
        }

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> tuple[Any, int]:
        url = UW_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self._key}",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read()), 200
        except urllib.error.HTTPError as e:
            _log.warning("[uw_hist] %s -> %d", path, e.code)
            return {}, e.code
        except Exception as exc:
            _log.warning("[uw_hist] %s -> error: %s", path, exc)
            return {}, 0

    @staticmethod
    def _extract_list(raw: Any) -> list[dict]:
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            d = raw.get("data", raw)
            return d if isinstance(d, list) else []
        return []

    def _overall_audit(self, endpoints: dict) -> dict[str, Any]:
        pit_count = sum(
            1 for ep in endpoints.values()
            if ep.get("audit", {}).get("point_in_time_safe")
        )
        total = len(endpoints)
        return {
            "total_endpoints_tried": total,
            "point_in_time_safe_count": pit_count,
            "quality": "good" if pit_count >= 4 else ("partial" if pit_count >= 2 else "poor"),
            "summary": {
                k: {
                    "status": "✅ pit-safe" if v.get("audit", {}).get("point_in_time_safe") else "❌ unavailable",
                    "note": v.get("audit", {}).get("note", ""),
                }
                for k, v in endpoints.items()
            },
        }

    def _save_snapshot(self, ticker: str, data: dict) -> None:
        path = _SNAP_DIR / f"{self.as_of}_{ticker}_uw_snapshot.json"
        try:
            path.write_text(json.dumps(data, indent=2, default=str))
            _log.info("[uw_hist] snapshot saved -> %s", path)
        except Exception as exc:
            _log.warning("[uw_hist] snapshot save failed: %s", exc)
