"""Masi Agent — LLM-powered SPX options signal agent.

Reads live 2-min SPX bars + /ES + /NQ futures data and reasons about
trade setups the way Masi would in his course, using:
  - VWAP reclaim/rejection
  - Trend line breaks (2+ touch)
  - Gap/wick fills
  - EMA cross confirmation (9/21)
  - Volume confirmation
  - /ES and /NQ futures alignment

Uses Kimi K2 via OpenAI-compatible API (Moonshot).
"""
from __future__ import annotations

import logging
import math
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

_log = logging.getLogger(__name__)

# Load env
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass

LLM_API_KEY  = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.moonshot.ai/v1")
LLM_MODEL    = os.environ.get("LLM_MODEL_NAME", "kimi-k2-0711-preview")

# Schwab for futures data
_SCHWAB_DIR = Path(__file__).resolve().parents[1] / "schwab-collector"
if str(_SCHWAB_DIR) not in sys.path:
    sys.path.insert(0, str(_SCHWAB_DIR))

from schwab_auth import get_valid_token  # noqa: E402


# ── Futures Data Fetcher ───────────────────────────────────────────────────────

def _fetch_futures_bars(symbol: str, freq_min: int = 5) -> list[dict]:
    """Fetch intraday bars for a futures symbol (/ES, /NQ)."""
    try:
        token = get_valid_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        r = requests.get(
            "https://api.schwabapi.com/marketdata/v1/pricehistory",
            headers=headers,
            params={
                "symbol": symbol,
                "periodType": "day",
                "period": 1,
                "frequencyType": "minute",
                "frequency": freq_min,
                "needExtendedHoursData": "true",
            },
            timeout=15,
        )
        candles = r.json().get("candles", [])
        return [
            {
                "dt": datetime.fromtimestamp(c["datetime"] / 1000, tz=timezone.utc).strftime("%H:%M"),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": int(c["volume"]),
            }
            for c in candles
        ]
    except Exception as e:
        _log.warning("[masi_agent] futures fetch failed for %s: %s", symbol, e)
        return []


def _fetch_futures_quote(symbol: str) -> dict:
    """Get current quote for a futures symbol."""
    try:
        token = get_valid_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        r = requests.get(
            "https://api.schwabapi.com/marketdata/v1/quotes",
            headers=headers,
            params={"symbols": symbol},
            timeout=10,
        )
        data = r.json()
        # Schwab returns /ESM26 etc — grab first key
        for key, val in data.items():
            if isinstance(val, dict) and "quote" in val:
                q = val["quote"]
                return {
                    "symbol": key,
                    "last": q.get("lastPrice") or q.get("mark", 0),
                    "mark": q.get("mark", 0),
                }
        return {}
    except Exception as e:
        _log.warning("[masi_agent] futures quote failed for %s: %s", symbol, e)
        return {}


# ── Indicator Helpers ──────────────────────────────────────────────────────────

def _compute_vwap(bars: list[dict]) -> float:
    cv = sum((b["high"] + b["low"] + b["close"]) / 3 * b["volume"] for b in bars)
    v = sum(b["volume"] for b in bars)
    return round(cv / v, 2) if v > 0 else (bars[-1]["close"] if bars else 0)


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return round(e, 2)


def _summarize_bars(bars: list[dict], label: str = "") -> str:
    """Format last N bars as a readable table for LLM context."""
    if not bars:
        return f"{label}: no data"
    lines = [f"{label} (last {len(bars)} bars, 5-min):"]
    lines.append("  Time   Open      High      Low       Close     Vol")
    for b in bars:
        lines.append(
            f"  {b['dt']}  {b['open']:>8.2f}  {b['high']:>8.2f}  "
            f"{b['low']:>8.2f}  {b['close']:>8.2f}  {b['volume']:>6}"
        )
    return "\n".join(lines)


# ── Masi Agent ─────────────────────────────────────────────────────────────────

class MasiAgent:
    """LLM agent that reads 2-min bars and applies Masi's trading rules."""

    def __init__(self) -> None:
        if not LLM_API_KEY:
            raise ValueError("LLM_API_KEY not set — Kimi API key required for Masi Agent")

    def _call_llm(self, prompt: str) -> str:
        resp = requests.post(
            f"{LLM_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": LLM_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are Masi, an expert SPX/SPY options day trader. "
                            "You trade using VWAP, 9/21 EMA, trend line breaks, gap fills, "
                            "and volume confirmation. You only take high-probability setups. "
                            "You always check /ES and /NQ futures alignment before entering. "
                            "You output concise, structured trading decisions."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 1,
                "max_tokens": 2048,
            },
            timeout=90,
        )
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        # Kimi K2 may return answer in content or reasoning_content
        content = msg.get("content", "").strip()
        if not content:
            content = msg.get("reasoning_content", "").strip()
        return content

    def analyze(
        self,
        spx_bars: list[dict],       # 2-min SPX bars
        es_bars: list[dict],         # 5-min /ES bars
        nq_bars: list[dict],         # 5-min /NQ bars
        spx_vwap: float,
        es_quote: dict,
        nq_quote: dict,
        morning_high: float,
        morning_low: float,
        uw_flow_bias: str,
        uw_signals: list[dict],
    ) -> dict[str, Any]:
        """Run Masi Agent analysis and return structured signal."""

        # Build bar summaries
        spx_last20 = spx_bars[-12:] if spx_bars else []  # trim to avoid token limit
        es_last10 = es_bars[-8:] if es_bars else []
        nq_last10 = nq_bars[-6:] if nq_bars else []

        # Compute EMAs for SPX
        spx_closes = [b["close"] for b in spx_bars]
        ema9  = _ema(spx_closes[-30:], 9)  if len(spx_closes) >= 9  else 0
        ema21 = _ema(spx_closes[-30:], 21) if len(spx_closes) >= 21 else 0

        # ES/NQ VWAP position
        es_vwap  = _compute_vwap(es_bars[-40:])  if es_bars  else 0
        nq_vwap  = _compute_vwap(nq_bars[-40:])  if nq_bars  else 0
        es_last  = es_quote.get("last", 0)
        nq_last  = nq_quote.get("last", 0)
        es_above = es_last > es_vwap if es_vwap else None
        nq_above = nq_last > nq_vwap if nq_vwap else None

        # Current SPX price and VWAP position
        spx_last = spx_closes[-1] if spx_closes else 0
        spx_above_vwap = spx_last > spx_vwap if spx_vwap else None

        # Volume check
        vols = [b["volume"] for b in spx_bars[-20:]]
        avg_vol = statistics.mean(vols[:-1]) if len(vols) > 1 else 1
        last_vol_ratio = round(vols[-1] / avg_vol, 2) if avg_vol > 0 else 1.0

        # UW signals summary
        uw_summary = ""
        if uw_signals:
            uw_summary = "; ".join([s.get("reason", "") for s in uw_signals[:3]])

        # Build prompt
        prompt = f"""
You are analyzing SPX for an intraday options trade right now.

=== FUTURES ALIGNMENT (Masi rule: check /ES and /NQ vs VWAP before any trade) ===
/ES: {es_last:.2f} | VWAP: {es_vwap:.2f} | Position: {"ABOVE" if es_above else "BELOW"} VWAP
/NQ: {nq_last:.2f} | VWAP: {nq_vwap:.2f} | Position: {"ABOVE" if nq_above else "BELOW"} VWAP

=== SPX INTRADAY (2-min bars) ===
Current Price: {spx_last:.2f}
VWAP: {spx_vwap:.2f} | Position: {"ABOVE" if spx_above_vwap else "BELOW"} VWAP
9 EMA: {ema9:.2f} | 21 EMA: {ema21:.2f} | EMA Cross: {"9 ABOVE 21 (bullish)" if ema9 > ema21 else "9 BELOW 21 (bearish)"}
Morning High: {morning_high:.2f} | Morning Low: {morning_low:.2f}
Last bar volume: {last_vol_ratio:.1f}x average

{_summarize_bars(spx_last20, "SPX 2-min bars")}

=== /ES 5-MIN BARS ===
{_summarize_bars(es_last10, "/ES")}

=== UNUSUAL WHALES FLOW ===
Flow Bias: {uw_flow_bias.upper()}
{f"Signals: {uw_summary}" if uw_summary else "No major sweeps detected"}

=== YOUR TASK ===
Apply your trading rules:
1. Are /ES AND /NQ both above or below VWAP? (must align for a trade)
2. Is SPX above or below its VWAP? (confirms direction)
3. Do you see a trend line forming (2+ touches)? If so, has it broken?
4. Is the 9 EMA above or below 21 EMA? (trend confirmation)
5. Is there a gap or wick to fill?
6. Does volume confirm the move?
7. Does UW flow support the direction?

Output EXACTLY in this format (no extra text):
ACTION: BUY_CALLS or BUY_PUTS or HOLD
ENTRY: <price>
TARGET_1: <price> (70% exit)
TARGET_2: <price> (30% runner)
STOP: <price>
CONFIDENCE: <percentage>
SETUP: <name of setup e.g. VWAP_RECLAIM, TREND_LINE_BREAK, GAP_FILL>
REASON: <one sentence explaining the trade in Masi's style>
"""

        try:
            raw = self._call_llm(prompt)
            return self._parse_response(raw, spx_last)
        except Exception as e:
            _log.error("[masi_agent] LLM call failed: %s", e)
            return {
                "action": "HOLD",
                "entry": spx_last,
                "target_1": 0,
                "target_2": 0,
                "stop": 0,
                "confidence": 0,
                "setup": "ERROR",
                "reason": str(e),
                "raw": "",
            }

    def _parse_response(self, raw: str, fallback_price: float) -> dict[str, Any]:
        """Parse LLM structured output into dict."""
        result = {
            "action": "HOLD",
            "entry": fallback_price,
            "target_1": 0.0,
            "target_2": 0.0,
            "stop": 0.0,
            "confidence": 50,
            "setup": "",
            "reason": "",
            "raw": raw,
        }
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("ACTION:"):
                val = line.split(":", 1)[1].strip()
                if "CALL" in val:
                    result["action"] = "BUY_CALLS"
                elif "PUT" in val:
                    result["action"] = "BUY_PUTS"
                else:
                    result["action"] = "HOLD"
            elif line.startswith("ENTRY:"):
                try:
                    result["entry"] = float(line.split(":", 1)[1].strip().split()[0])
                except Exception:
                    pass
            elif line.startswith("TARGET_1:"):
                try:
                    result["target_1"] = float(line.split(":", 1)[1].strip().split()[0])
                except Exception:
                    pass
            elif line.startswith("TARGET_2:"):
                try:
                    result["target_2"] = float(line.split(":", 1)[1].strip().split()[0])
                except Exception:
                    pass
            elif line.startswith("STOP:"):
                try:
                    result["stop"] = float(line.split(":", 1)[1].strip().split()[0])
                except Exception:
                    pass
            elif line.startswith("CONFIDENCE:"):
                try:
                    result["confidence"] = int(line.split(":", 1)[1].strip().replace("%", ""))
                except Exception:
                    pass
            elif line.startswith("SETUP:"):
                result["setup"] = line.split(":", 1)[1].strip()
            elif line.startswith("REASON:"):
                result["reason"] = line.split(":", 1)[1].strip()
        return result


# ── Standalone Runner ──────────────────────────────────────────────────────────

def run_masi_agent(
    spx_intraday: dict,
    uw_data: dict,
    spx_vwap: float = 0.0,
) -> dict[str, Any]:
    """
    Top-level function — fetches /ES + /NQ, runs Masi Agent.

    Args:
        spx_intraday: Output of SchwabIntradayService.collect("SPX" or "SPY")
        uw_data:      Output of UWCollectorService.collect()
        spx_vwap:     Current SPX VWAP (from intraday service)

    Returns:
        Masi Agent signal dict
    """
    _log.info("[masi_agent] fetching /ES and /NQ data...")
    es_bars  = _fetch_futures_bars("/ES", freq_min=5)
    nq_bars  = _fetch_futures_bars("/NQ", freq_min=5)
    es_quote = _fetch_futures_quote("/ES")
    nq_quote = _fetch_futures_quote("/NQ")

    bars       = spx_intraday.get("bars_sample", [])
    levels     = spx_intraday.get("levels", {})
    current    = spx_intraday.get("current", {})
    vwap       = spx_vwap or current.get("vwap", 0)
    m_high     = levels.get("morning_high", 0)
    m_low      = levels.get("morning_low", 0)
    uw_bias    = uw_data.get("flow_bias", "neutral")
    uw_signals = uw_data.get("signals", [])

    agent = MasiAgent()
    result = agent.analyze(
        spx_bars=bars,
        es_bars=es_bars,
        nq_bars=nq_bars,
        spx_vwap=vwap,
        es_quote=es_quote,
        nq_quote=nq_quote,
        morning_high=m_high,
        morning_low=m_low,
        uw_flow_bias=uw_bias,
        uw_signals=uw_signals,
    )
    result["es_quote"] = es_quote
    result["nq_quote"] = nq_quote
    result["es_vwap"]  = _compute_vwap(es_bars[-40:]) if es_bars else 0
    result["nq_vwap"]  = _compute_vwap(nq_bars[-40:]) if nq_bars else 0
    return result


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)

    # Quick standalone test
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "schwab-collector"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "uw-collector"))

    from schwab_intraday_service import SchwabIntradayService
    from uw_collector_service import UWCollectorService

    print("Fetching SPY intraday + UW data...")
    intraday = SchwabIntradayService(frequency_minutes=5).collect("SPY")
    uw       = UWCollectorService().collect("SPY", current_price=intraday["current"]["price"])

    print("Running Masi Agent (Kimi K2)...")
    result = run_masi_agent(intraday, uw, spx_vwap=intraday["current"]["vwap"])

    print("\n" + "=" * 55)
    print("MASI AGENT SIGNAL")
    print("=" * 55)
    print(f"Action:     {result['action']}")
    print(f"Setup:      {result['setup']}")
    print(f"Entry:      ${result['entry']:.2f}")
    print(f"Target 1:   ${result['target_1']:.2f}  (70% exit)")
    print(f"Target 2:   ${result['target_2']:.2f}  (30% runner)")
    print(f"Stop:       ${result['stop']:.2f}")
    print(f"Confidence: {result['confidence']}%")
    print(f"Reason:     {result['reason']}")
    print(f"\n/ES: {result['es_quote'].get('last')} (VWAP {result['es_vwap']})")
    print(f"/NQ: {result['nq_quote'].get('last')} (VWAP {result['nq_vwap']})")
    print(f"\nRaw LLM output:\n{result['raw']}")
