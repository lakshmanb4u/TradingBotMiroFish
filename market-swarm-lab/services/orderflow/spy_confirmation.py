# services/orderflow/spy_confirmation.py
"""
SPY confirmation overlay for ES sweep/reclaim events.

Requirements:
- SPY 1-min bars (from Schwab or other source)
- Timestamp alignment within 60s of ES event
- VWAP, EMA9/21, volume, prior day high/low, premarket high/low
"""
from __future__ import annotations
import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime, timezone
import statistics

@dataclass
class SPYBar:
    ts: str          # ISO8601
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float
    ema9: float
    ema21: float

@dataclass
class ConfirmationSignal:
    es_ts: str
    es_direction: str
    es_level: float
    es_trigger_price: float
    spy_bar_ts: str
    spy_close: float
    spy_vwap: float
    spy_ema9: float
    spy_ema21: float
    spy_volume_vs_avg: float
    signal: str      # CALL | PUT | NO_CONFIRM
    confidence: float
    entry: float
    stop: float
    target_1: float
    target_2: float
    invalidation_reason: str = ""

class SPYConfirmation:
    def __init__(self, max_lag_sec: float = 60.0, volume_lookback: int = 20,
                 risk_reward_1: float = 1.0, risk_reward_2: float = 2.0):
        self.max_lag_sec = max_lag_sec
        self.volume_lookback = volume_lookback
        self.risk_reward = (risk_reward_1, risk_reward_2)
        self.spy_bars: List[SPYBar] = []
        self.vol_avg: float = 0.0

    def load_spy_bars(self, bars: List[Dict]):
        """bars: list of dicts with open/high/low/close/volume/ts keys"""
        self.spy_bars = []
        vols = []
        for b in bars:
            bar = SPYBar(
                ts=b.get("ts", b.get("timestamp", "")),
                open=b.get("open", 0.0),
                high=b.get("high", 0.0),
                low=b.get("low", 0.0),
                close=b.get("close", 0.0),
                volume=b.get("volume", 0),
                vwap=b.get("vwap", b.get("close", 0.0)),
                ema9=b.get("ema9", b.get("close", 0.0)),
                ema21=b.get("ema21", b.get("close", 0.0)),
            )
            self.spy_bars.append(bar)
            vols.append(bar.volume)
        self.vol_avg = statistics.mean(vols[-self.volume_lookback:]) if len(vols) >= self.volume_lookback else statistics.mean(vols)

    def confirm(self, es_event: Dict) -> ConfirmationSignal:
        """For a single ES sweep event, find nearest SPY bar and confirm/reject."""
        es_ts = es_event.get("ts_event", "")
        direction = es_event.get("direction", "")
        level = es_event.get("level", 0.0)
        trigger = es_event.get("trigger_price", 0.0)

        spy_bar = self._find_nearest_spy_bar(es_ts)
        if spy_bar is None:
            return self._no_confirm(es_ts, direction, level, trigger, "No SPY bar within max_lag")

        # Volume check
        vol_ratio = spy_bar.volume / max(self.vol_avg, 1)
        if vol_ratio < 0.8:
            return self._no_confirm(es_ts, direction, level, trigger, "SPY volume below average")

        # EMA alignment
        ema_bullish = spy_bar.ema9 >= spy_bar.ema21
        ema_bearish = spy_bar.ema9 <= spy_bar.ema21

        # VWAP check
        above_vwap = spy_bar.close > spy_bar.vwap
        below_vwap = spy_bar.close < spy_bar.vwap

        delta = abs(trigger - level)
        if direction == "bullish_sweep":
            if above_vwap and ema_bullish:
                signal = "CALL"
                confidence = 0.7 + min(vol_ratio - 1.0, 0.2)
                entry = spy_bar.close
                stop = trigger - delta * 0.5
                target_1 = entry + (entry - stop) * self.risk_reward[0]
                target_2 = entry + (entry - stop) * self.risk_reward[1]
            else:
                signal = "NO_CONFIRM"
                confidence = 0.0
                entry = stop = target_1 = target_2 = 0.0
                invalid = "No bullish alignment" if not above_vwap else "EMA bearish"
                return self._no_confirm(es_ts, direction, level, trigger, invalid)
        elif direction == "bearish_sweep":
            if below_vwap and ema_bearish:
                signal = "PUT"
                confidence = 0.7 + min(vol_ratio - 1.0, 0.2)
                entry = spy_bar.close
                stop = trigger + delta * 0.5
                target_1 = entry - (stop - entry) * self.risk_reward[0]
                target_2 = entry - (stop - entry) * self.risk_reward[1]
            else:
                signal = "NO_CONFIRM"
                confidence = 0.0
                entry = stop = target_1 = target_2 = 0.0
                invalid = "No bearish alignment" if not below_vwap else "EMA bullish"
                return self._no_confirm(es_ts, direction, level, trigger, invalid)
        else:
            return self._no_confirm(es_ts, direction, level, trigger, "Unknown direction")

        return ConfirmationSignal(
            es_ts=es_ts, es_direction=direction, es_level=level,
            es_trigger_price=trigger, spy_bar_ts=spy_bar.ts,
            spy_close=spy_bar.close, spy_vwap=spy_bar.vwap,
            spy_ema9=spy_bar.ema9, spy_ema21=spy_bar.ema21,
            spy_volume_vs_avg=round(vol_ratio, 2),
            signal=signal, confidence=round(confidence, 2),
            entry=round(entry, 2), stop=round(stop, 2),
            target_1=round(target_1, 2), target_2=round(target_2, 2),
            invalidation_reason="",
        )

    def _find_nearest_spy_bar(self, es_ts: str) -> Optional[SPYBar]:
        try:
            es_dt = datetime.fromisoformat(es_ts.replace("Z", "+00:00"))
        except Exception:
            return None
        best = None
        best_diff = float("inf")
        for bar in self.spy_bars:
            try:
                bar_dt = datetime.fromisoformat(bar.ts.replace("Z", "+00:00"))
                diff = abs((bar_dt - es_dt).total_seconds())
                if diff < best_diff and diff <= self.max_lag_sec:
                    best = bar
                    best_diff = diff
            except Exception:
                continue
        return best

    def _no_confirm(self, es_ts, direction, level, trigger, reason) -> ConfirmationSignal:
        return ConfirmationSignal(
            es_ts=es_ts, es_direction=direction, es_level=level,
            es_trigger_price=trigger, spy_bar_ts="", spy_close=0.0,
            spy_vwap=0.0, spy_ema9=0.0, spy_ema21=0.0,
            spy_volume_vs_avg=0.0, signal="NO_CONFIRM", confidence=0.0,
            entry=0.0, stop=0.0, target_1=0.0, target_2=0.0,
            invalidation_reason=reason,
        )


def load_spy_bars_from_csv(csv_path: str) -> List[Dict]:
    import csv
    bars = []
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bars.append({
                "ts": row.get("timestamp", row.get("ts", "")),
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": float(row.get("close", 0)),
                "volume": int(row.get("volume", 0)),
                "vwap": float(row.get("vwap", row.get("close", 0))),
                "ema9": float(row.get("ema9", row.get("close", 0))),
                "ema21": float(row.get("ema21", row.get("close", 0))),
            })
    return bars


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--es-events", required=True, help="Path to es_sweep_events.json or CSV")
    parser.add_argument("--spy-bars", required=True, help="Path to SPY 1min bars CSV")
    parser.add_argument("--output", default="spy_confirmations.csv")
    args = parser.parse_args()

    # Load ES events
    with open(args.es_events, "r") as f:
        es_events = json.load(f)

    spy_bars = load_spy_bars_from_csv(args.spy_bars)
    conf = SPYConfirmation()
    conf.load_spy_bars(spy_bars)

    results = []
    for ev in es_events:
        sig = conf.confirm(ev)
        results.append(sig)

    with open(args.output, "w") as f:
        f.write("es_ts,es_direction,es_level,es_trigger_price,spy_bar_ts,spy_close,spy_vwap,"
                "spy_ema9,spy_ema21,spy_volume_vs_avg,signal,confidence,entry,stop,target_1,target_2,invalidation_reason\n")
        for r in results:
            f.write(f"{r.es_ts},{r.es_direction},{r.es_level},{r.es_trigger_price},"
                    f"{r.spy_bar_ts},{r.spy_close},{r.spy_vwap},{r.spy_ema9},{r.spy_ema21},"
                    f"{r.spy_volume_vs_avg},{r.signal},{r.confidence},{r.entry},{r.stop},"
                    f"{r.target_1},{r.target_2},{r.invalidation_reason}\n")

    call_count = sum(1 for r in results if r.signal == "CALL")
    put_count = sum(1 for r in results if r.signal == "PUT")
    print(f"SPY confirmations: {call_count} CALL, {put_count} PUT. Written to {args.output}")
