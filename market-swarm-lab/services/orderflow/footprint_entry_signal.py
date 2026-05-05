#!/usr/bin/env python3
"""
footprint_entry_signal.py
Reddit-style footprint entry signal generator.

Entry signals combine four concepts:
1. Marked structural levels (support/resistance)
2. Divergence (candle color vs delta)
3. Absorption at the level
4. Reclaim/rejection confirmation

LONG criteria:
  - Price is at/near a marked support level
  - Divergence: red candle with positive delta (sellers exhausted, buyers absorbing)
    OR weak lower low with diminishing sell pressure
  - Bullish absorption detected at/near the level
  - Price reclaims above the level (close above support)

SHORT criteria:
  - Price is at/near a marked resistance level
  - Divergence: green candle with negative delta (buyers exhausted, sellers selling)
    OR weak higher high with diminishing buy pressure
  - Bearish absorption detected at/near the level
  - Price rejects from the level (close below resistance)

Output:
  - CSV: state/orderflow/live/footprint_entry_candidates.csv
  - MD:  state/orderflow/live/footprint_diagnostics.md
  - JSON: state/orderflow/live/footprint_latest_candidate.json
"""
from __future__ import annotations

import json
import csv
import os
import math
import argparse
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
from datetime import datetime, timezone
from pathlib import Path

from marked_levels import MarkedLevelsDetector, MarkedLevel
from absorption_detector import AbsorptionDetector, AbsorptionEvent
from tick_footprint_builder import TickFootprintCandle


# ─── Paths ──────────────────────────────────────────────────────────────────
DEFAULT_STATE_DIR = Path("state/orderflow/live")
CSV_PATH = DEFAULT_STATE_DIR / "footprint_entry_candidates.csv"
MD_PATH = DEFAULT_STATE_DIR / "footprint_diagnostics.md"
JSON_PATH = DEFAULT_STATE_DIR / "footprint_latest_candidate.json"

ES_TICK_SIZE = 0.25
NQ_TICK_SIZE = 0.50


@dataclass
class FootprintEntrySignal:
    """A complete entry signal derived from footprint analysis."""
    ts_generated: str
    ts_event: str
    direction: str                     # LONG | SHORT
    confidence: float                  # 0 - 100
    entry_price: float
    trigger_level: float
    level_type: str
    setup_type: str                    # e.g., "support_divergence_absorption_reclaim"

    # Footprint components
    divergence_detected: bool = False
    divergence_type: str = ""
    absorption_detected: bool = False
    absorption_score: float = 0.0
    reclaim_rejection: bool = False

    # Candle context
    candle_open: float = 0.0
    candle_high: float = 0.0
    candle_low: float = 0.0
    candle_close: float = 0.0
    candle_delta: int = 0
    candle_vol: int = 0

    # Level context
    touches: int = 0
    level_strength: float = 0.0

    # Scoring breakdown
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


class FootprintEntrySignalGenerator:
    """
    Generate entry signals from footprint candles using marked levels,
    divergence, absorption, and reclaim/rejection.
    """

    def __init__(
        self,
        min_confidence: float = 50.0,
        prox_ticks: int = 4,
        ticks_per_candle: int = 20,
        opening_range_candles: int = 20,
    ):
        self.min_confidence = min_confidence
        self.prox_ticks = prox_ticks
        self.ticks_per_candle = ticks_per_candle
        self.opening_range_candles = opening_range_candles
        self.signals: List[FootprintEntrySignal] = []
        self._tick_size = ES_TICK_SIZE

    # ─── Public API ──────────────────────────────────────────────────────

    def generate(self, candles: List[TickFootprintCandle]) -> List[FootprintEntrySignal]:
        """
        Run full signal generation pipeline on tick footprint candles.
        """
        if not candles or len(candles) < 10:
            self.signals = []
            return []

        self._tick_size = self._infer_tick_size(candles)
        self.signals = []

        # 1. Detect structural levels
        level_detector = MarkedLevelsDetector(
            opening_range_candles=self.opening_range_candles,
            prox_threshold_ticks=self.prox_ticks,
        )
        all_levels = level_detector.analyze(candles)
        support_levels = level_detector.get_support_levels()
        resistance_levels = level_detector.get_resistance_levels()
        vwap_levels = level_detector.get_levels_by_type("vwap")
        poc_levels = level_detector.get_levels_by_type("poc")

        # 2. Detect absorption events
        abs_detector = AbsorptionDetector(
            lookback_candles=5,
            stall_threshold_ticks=2.0,
            prox_ticks=self.prox_ticks,
            min_absorption_score=25.0,
        )
        abs_events = abs_detector.analyze(candles, marked_levels=all_levels)

        # 3. Evaluate each candle for entry criteria
        for i in range(5, len(candles)):
            c = candles[i]
            recent = candles[i - 5:i]

            # ── LONG at support ──
            long_signal = self._evaluate_long(c, recent, support_levels, vwap_levels, poc_levels, abs_events, all_levels)
            if long_signal and long_signal.confidence >= self.min_confidence:
                self.signals.append(long_signal)

            # ── SHORT at resistance ──
            short_signal = self._evaluate_short(c, recent, resistance_levels, vwap_levels, poc_levels, abs_events, all_levels)
            if short_signal and short_signal.confidence >= self.min_confidence:
                self.signals.append(short_signal)

        # Sort by confidence descending
        self.signals.sort(key=lambda s: s.confidence, reverse=True)
        return self.signals

    def write_outputs(self, signals: Optional[List[FootprintEntrySignal]] = None):
        """Write all diagnostic outputs (CSV, MD, JSON)."""
        if signals is None:
            signals = self.signals
        self._write_csv(signals)
        self._write_md(signals)
        self._write_json(signals)

    # ─── Evaluation ──────────────────────────────────────────────────────

    def _evaluate_long(
        self,
        current: TickFootprintCandle,
        recent: List[TickFootprintCandle],
        support_levels: List[MarkedLevel],
        vwap_levels: List[MarkedLevel],
        poc_levels: List[MarkedLevel],
        abs_events: List[AbsorptionEvent],
        all_levels: List[MarkedLevel],
    ) -> Optional[FootprintEntrySignal]:
        """
        LONG: marked support + divergence + absorption + reclaim
        """
        # Find nearest support level
        nearest_support = self._find_nearest_level(current.low, support_levels + vwap_levels + poc_levels)
        if nearest_support is None:
            return None

        # Must be near the support level
        if not self._price_near(current.low, nearest_support.price):
            return None

        # Divergence check
        divergence = self._check_bullish_divergence(current, recent)
        if not divergence["detected"]:
            return None

        # Absorption check at/near this level
        abs_event = self._find_absorption_at_level(abs_events, nearest_support.price, "bullish")
        if abs_event is None:
            return None

        # Reclaim: price closes back above the support level
        reclaim = current.close > nearest_support.price
        reclaim_strong = reclaim and current.close > current.open_price

        # ── Score calculation ──
        score_breakdown: Dict[str, float] = {}
        score = 30.0  # base

        # Level strength (0-1)
        level_str = nearest_support.strength
        score += level_str * 20
        score_breakdown["level_strength"] = round(level_str * 20, 1)

        # Divergence type weighting
        div_type = divergence.get("type", "")
        if div_type == "red_body_positive_delta":
            score += 15
            score_breakdown["divergence"] = 15.0
        elif div_type == "weak_lower_low":
            score += 10
            score_breakdown["divergence"] = 10.0
        else:
            score += 8
            score_breakdown["divergence"] = 8.0

        # Absorption score contribution
        abs_contrib = min(abs_event.score * 0.2, 20)
        score += abs_contrib
        score_breakdown["absorption"] = round(abs_contrib, 1)

        # Reclaim bonus
        if reclaim_strong:
            score += 15
            score_breakdown["reclaim"] = 15.0
        elif reclaim:
            score += 8
            score_breakdown["reclaim"] = 8.0
        else:
            score_breakdown["reclaim"] = 0.0

        # Multiple touches at level
        touches = nearest_support.touches
        if touches >= 3:
            score += 5
            score_breakdown["touches"] = 5.0
        elif touches >= 1:
            score += 2
            score_breakdown["touches"] = 2.0

        # Volume confirmation (larger candle volume = more reliable)
        vol = current.total_vol
        if vol > 200:
            score += 3
            score_breakdown["volume"] = 3.0
        elif vol > 100:
            score += 1.5
            score_breakdown["volume"] = 1.5

        score = min(score, 100.0)
        if score < self.min_confidence:
            return None

        # Build setup type label
        parts = []
        if nearest_support.level_type in ("session_low", "opening_range_low"):
            parts.append("support")
        elif nearest_support.level_type == "vwap":
            parts.append("vwap")
        elif nearest_support.level_type == "poc":
            parts.append("poc")
        else:
            parts.append(nearest_support.level_type)
        if divergence["detected"]:
            parts.append("divergence")
        if abs_event:
            parts.append("absorption")
        if reclaim:
            parts.append("reclaim" if reclaim_strong else "weak_reclaim")
        setup_type = "_".join(parts)

        return FootprintEntrySignal(
            ts_generated=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            ts_event=current.ts_close,
            direction="LONG",
            confidence=round(score, 1),
            entry_price=current.close,
            trigger_level=nearest_support.price,
            level_type=nearest_support.level_type,
            setup_type=setup_type,
            divergence_detected=divergence["detected"],
            divergence_type=div_type,
            absorption_detected=True,
            absorption_score=abs_event.score,
            reclaim_rejection=reclaim,
            candle_open=current.open_price,
            candle_high=current.high,
            candle_low=current.low,
            candle_close=current.close,
            candle_delta=current.total_delta,
            candle_vol=current.total_vol,
            touches=touches,
            level_strength=level_str,
            score_breakdown=score_breakdown,
            metadata={
                "abs_event_ts": abs_event.ts,
                "abs_sell_vol": abs_event.sell_vol,
                "abs_buy_vol": abs_event.buy_vol,
                "stall_ticks": abs_event.price_stall_ticks,
                "abs_touch_count": abs_event.touch_count,
            },
        )

    def _evaluate_short(
        self,
        current: TickFootprintCandle,
        recent: List[TickFootprintCandle],
        resistance_levels: List[MarkedLevel],
        vwap_levels: List[MarkedLevel],
        poc_levels: List[MarkedLevel],
        abs_events: List[AbsorptionEvent],
        all_levels: List[MarkedLevel],
    ) -> Optional[FootprintEntrySignal]:
        """
        SHORT: marked resistance + divergence + absorption + rejection
        """
        nearest_resistance = self._find_nearest_level(current.high, resistance_levels + vwap_levels + poc_levels)
        if nearest_resistance is None:
            return None

        if not self._price_near(current.high, nearest_resistance.price):
            return None

        # Divergence check
        divergence = self._check_bearish_divergence(current, recent)
        if not divergence["detected"]:
            return None

        # Absorption check
        abs_event = self._find_absorption_at_level(abs_events, nearest_resistance.price, "bearish")
        if abs_event is None:
            return None

        # Rejection: price closes back below the resistance level
        rejection = current.close < nearest_resistance.price
        rejection_strong = rejection and current.close < current.open_price

        # ── Score ──
        score_breakdown: Dict[str, float] = {}
        score = 30.0

        level_str = nearest_resistance.strength
        score += level_str * 20
        score_breakdown["level_strength"] = round(level_str * 20, 1)

        div_type = divergence.get("type", "")
        if div_type == "green_body_negative_delta":
            score += 15
            score_breakdown["divergence"] = 15.0
        elif div_type == "weak_higher_high":
            score += 10
            score_breakdown["divergence"] = 10.0
        else:
            score += 8
            score_breakdown["divergence"] = 8.0

        abs_contrib = min(abs_event.score * 0.2, 20)
        score += abs_contrib
        score_breakdown["absorption"] = round(abs_contrib, 1)

        if rejection_strong:
            score += 15
            score_breakdown["rejection"] = 15.0
        elif rejection:
            score += 8
            score_breakdown["rejection"] = 8.0
        else:
            score_breakdown["rejection"] = 0.0

        touches = nearest_resistance.touches
        if touches >= 3:
            score += 5
            score_breakdown["touches"] = 5.0
        elif touches >= 1:
            score += 2
            score_breakdown["touches"] = 2.0

        vol = current.total_vol
        if vol > 200:
            score += 3
            score_breakdown["volume"] = 3.0
        elif vol > 100:
            score += 1.5
            score_breakdown["volume"] = 1.5

        score = min(score, 100.0)
        if score < self.min_confidence:
            return None

        parts = []
        if nearest_resistance.level_type in ("session_high", "opening_range_high"):
            parts.append("resistance")
        elif nearest_resistance.level_type == "vwap":
            parts.append("vwap")
        elif nearest_resistance.level_type == "poc":
            parts.append("poc")
        else:
            parts.append(nearest_resistance.level_type)
        if divergence["detected"]:
            parts.append("divergence")
        if abs_event:
            parts.append("absorption")
        if rejection:
            parts.append("rejection" if rejection_strong else "weak_rejection")
        setup_type = "_".join(parts)

        return FootprintEntrySignal(
            ts_generated=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            ts_event=current.ts_close,
            direction="SHORT",
            confidence=round(score, 1),
            entry_price=current.close,
            trigger_level=nearest_resistance.price,
            level_type=nearest_resistance.level_type,
            setup_type=setup_type,
            divergence_detected=divergence["detected"],
            divergence_type=div_type,
            absorption_detected=True,
            absorption_score=abs_event.score,
            reclaim_rejection=rejection,
            candle_open=current.open_price,
            candle_high=current.high,
            candle_low=current.low,
            candle_close=current.close,
            candle_delta=current.total_delta,
            candle_vol=current.total_vol,
            touches=touches,
            level_strength=level_str,
            score_breakdown=score_breakdown,
            metadata={
                "abs_event_ts": abs_event.ts,
                "abs_buy_vol": abs_event.buy_vol,
                "abs_sell_vol": abs_event.sell_vol,
                "stall_ticks": abs_event.price_stall_ticks,
                "abs_touch_count": abs_event.touch_count,
            },
        )

    # ─── Divergence checks ───────────────────────────────────────────────

    def _check_bullish_divergence(self, current: TickFootprintCandle, recent: List[TickFootprintCandle]) -> Dict:
        """
        Bullish divergence signatures:
        A) Red candle with positive delta (selling pressure but buyers absorbing)
        B) Weak lower low — new low but less aggressive selling vs prior low(s)
        """
        result = {"detected": False, "type": "", "details": ""}

        # A) Red body + positive delta
        if current.is_red and current.total_delta > 0:
            result["detected"] = True
            result["type"] = "red_body_positive_delta"
            result["details"] = f"red candle + delta {current.total_delta}"
            return result

        # B) Weak lower low
        if len(recent) >= 3:
            prior_lows = [c.low for c in recent]
            min_prior = min(prior_lows)
            if current.low < min_prior - self._tick_size * 0.5:
                # New lower low — check if delta is less negative
                prior_deltas = [c.total_delta for c in recent[-3:] if c.total_delta < 0]
                if prior_deltas and current.total_delta > sum(prior_deltas) / len(prior_deltas):
                    result["detected"] = True
                    result["type"] = "weak_lower_low"
                    result["details"] = "new low with less negative delta"
                    return result

        return result

    def _check_bearish_divergence(self, current: TickFootprintCandle, recent: List[TickFootprintCandle]) -> Dict:
        """
        Bearish divergence signatures:
        A) Green candle with negative delta (buying but sellers overwhelming)
        B) Weak higher high — new high but less aggressive buying vs prior high(s)
        """
        result = {"detected": False, "type": "", "details": ""}

        # A) Green body + negative delta
        if current.is_green and current.total_delta < 0:
            result["detected"] = True
            result["type"] = "green_body_negative_delta"
            result["details"] = f"green candle + delta {current.total_delta}"
            return result

        # B) Weak higher high
        if len(recent) >= 3:
            prior_highs = [c.high for c in recent]
            max_prior = max(prior_highs)
            if current.high > max_prior + self._tick_size * 0.5:
                prior_deltas = [c.total_delta for c in recent[-3:] if c.total_delta > 0]
                if prior_deltas and current.total_delta < sum(prior_deltas) / len(prior_deltas):
                    result["detected"] = True
                    result["type"] = "weak_higher_high"
                    result["details"] = "new high with less positive delta"
                    return result

        return result

    def _find_absorption_at_level(
        self,
        events: List[AbsorptionEvent],
        price: float,
        direction: str,
    ) -> Optional[AbsorptionEvent]:
        """Find the highest-scoring absorption event near the given price."""
        threshold = self.prox_ticks * self._tick_size
        matches = [e for e in events if e.direction == direction and abs(e.level_price - price) <= threshold]
        if not matches:
            return None
        return max(matches, key=lambda e: e.score)

    def _find_nearest_level(self, price: float, levels: List[MarkedLevel]) -> Optional[MarkedLevel]:
        threshold = self.prox_ticks * self._tick_size
        best = None
        best_dist = float("inf")
        for lv in levels:
            dist = abs(price - lv.price)
            if dist <= threshold and dist < best_dist:
                best_dist = dist
                best = lv
        return best

    def _price_near(self, price: float, ref: float) -> bool:
        return abs(price - ref) <= self.prox_ticks * self._tick_size

    def _infer_tick_size(self, candles: List) -> float:
        for c in candles:
            price = getattr(c, 'close', 0)
            if price > 10000:
                return NQ_TICK_SIZE
            if 1000 < price < 10000:
                return ES_TICK_SIZE
        return ES_TICK_SIZE

    # ─── Writers ─────────────────────────────────────────────────────────

    def _write_csv(self, signals: List[FootprintEntrySignal]):
        DEFAULT_STATE_DIR.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "ts_generated", "ts_event", "direction", "confidence",
            "entry_price", "trigger_level", "level_type", "setup_type",
            "divergence_detected", "divergence_type",
            "absorption_detected", "absorption_score",
            "reclaim_rejection", "candle_open", "candle_high",
            "candle_low", "candle_close", "candle_delta", "candle_vol",
            "touches", "level_strength", "score_breakdown", "metadata",
        ]
        with open(CSV_PATH, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for sig in signals:
                row = asdict(sig)
                row["score_breakdown"] = json.dumps(sig.score_breakdown)
                row["metadata"] = json.dumps(sig.metadata)
                writer.writerow(row)

    def _write_md(self, signals: List[FootprintEntrySignal]):
        DEFAULT_STATE_DIR.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Footprint Entry Diagnostics",
            "",
            f"**Generated:** {datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}Z",
            f"**Total candidates:** {len(signals)}",
            "",
        ]

        if not signals:
            lines.append("_No signals above confidence threshold._")
        else:
            # Summary stats
            avg_conf = sum(s.confidence for s in signals) / len(signals)
            longs = len([s for s in signals if s.direction == "LONG"])
            shorts = len([s for s in signals if s.direction == "SHORT"])
            lines.extend([
                "## Summary",
                "",
                f"- Average confidence: **{avg_conf:.1f}**",
                f"- LONG: {longs}  |  SHORT: {shorts}",
                f"- At 50+ confidence: {len([s for s in signals if s.confidence >= 50])}",
                f"- At 60+ confidence: {len([s for s in signals if s.confidence >= 60])}",
                f"- At 75+ confidence: {len([s for s in signals if s.confidence >= 75])}",
                "",
                "## Top Candidates",
                "",
            ])

            for rank, sig in enumerate(signals[:20], 1):
                lines.extend([
                    f"### #{rank} {sig.direction} @ {sig.entry_price:.2f}  (conf: {sig.confidence:.1f})",
                    "",
                    f"- **Setup:** {sig.setup_type}",
                    f"- **Level:** {sig.trigger_level:.2f} ({sig.level_type})",
                    f"- **TS:** {sig.ts_event}",
                    f"- **Candle:** O {sig.candle_open:.2f} H {sig.candle_high:.2f} L {sig.candle_low:.2f} C {sig.candle_close:.2f}",
                    f"- **Delta:** {sig.candle_delta}  |  **Vol:** {sig.candle_vol}",
                    f"- **Divergence:** {sig.divergence_type}",
                    f"- **Absorption score:** {sig.absorption_score:.1f}",
                    f"- **Reclaim/Rejection:** {sig.reclaim_rejection}",
                    f"- **Score breakdown:** {sig.score_breakdown}",
                    "",
                ])

        with open(MD_PATH, "w") as f:
            f.write("\n".join(lines))

    def _write_json(self, signals: List[FootprintEntrySignal]):
        DEFAULT_STATE_DIR.mkdir(parents=True, exist_ok=True)
        if signals:
            latest = signals[0]
            payload = {
                "ts_generated": latest.ts_generated,
                "ts_event": latest.ts_event,
                "direction": latest.direction,
                "confidence": latest.confidence,
                "entry_price": latest.entry_price,
                "trigger_level": latest.trigger_level,
                "level_type": latest.level_type,
                "setup_type": latest.setup_type,
                "divergence_type": latest.divergence_type,
                "absorption_score": latest.absorption_score,
                "reclaim_rejection": latest.reclaim_rejection,
                "candle": {
                    "o": latest.candle_open, "h": latest.candle_high,
                    "l": latest.candle_low, "c": latest.candle_close,
                    "delta": latest.candle_delta, "vol": latest.candle_vol,
                },
                "metadata": latest.metadata,
            }
        else:
            payload = {"direction": "NONE", "confidence": 0, "note": "No signals above threshold"}

        with open(JSON_PATH, "w") as f:
            json.dump(payload, f, indent=2)


# ─── CLI ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to tick_footprint.json")
    parser.add_argument("--min-confidence", type=float, default=50.0)
    parser.add_argument("--output-json", default=str(JSON_PATH))
    args = parser.parse_args()

    import sys
    sys.path.insert(0, ".")
    from tick_footprint_builder import TickFootprintBuilder, TickFootprintCandle, FootprintLadder

    with open(args.input) as f:
        data = json.load(f)

    candles = []
    for cj in data.get("candles", []):
        c = TickFootprintCandle(
            ts_open=cj.get("ts_open", ""), ts_close=cj.get("ts_close", ""),
            open_price=cj.get("open", cj.get("open_price", 0)),
            high=cj.get("high", 0), low=cj.get("low", 0), close=cj.get("close", 0),
            total_vol=cj.get("total_vol", cj.get("volume", 0)),
            total_delta=cj.get("total_delta", cj.get("delta", 0)),
            aggressive_buy_vol=cj.get("aggressive_buy_vol", 0),
            aggressive_sell_vol=cj.get("aggressive_sell_vol", 0),
            ticks=cj.get("ticks", 0),
        )
        for price, ld in cj.get("ladder", {}).items():
            p = float(price)
            c.ladder[p] = FootprintLadder(
                price=p, buy_vol=ld.get("buy_vol", 0), sell_vol=ld.get("sell_vol", 0),
                total_vol=ld.get("total_vol", 0), delta=ld.get("delta", 0),
            )
        candles.append(c)

    gen = FootprintEntrySignalGenerator(min_confidence=args.min_confidence)
    signals = gen.generate(candles)
    gen.write_outputs(signals)

    print(f"Signals generated: {len(signals)}")
    for sig in signals[:5]:
        print(f"  {sig.direction:5s} @ {sig.entry_price:.2f}  conf={sig.confidence:.1f}  setup={sig.setup_type}")
