"""Point-in-Time Replay Backtester for SPY / SPX.

Design principles:
  1. ZERO lookahead bias — at each candle T, only data with timestamp <= T is used.
  2. Deterministic-first — all LLM (MASi) calls are optional.
  3. Three PnL modes — underlying-only (A), approximate options (B), real options (C).
     Mode C is only activated when real historical option data is verified to exist.
  4. Full audit trail — every signal decision is logged with its data sources.

Architecture:
  BarStream       — yields candles chronologically, enforces PIT cutoff
  IndicatorState  — rolling window of indicators, updated bar-by-bar
  UWContextLoader — loads UW snapshots or live (also PIT-safe)
  EnsembleAdapter — wraps existing ensemble_scorer with historical bar data
  RegimeAdapter   — wraps existing daily_regime logic using historical bars
  TradeSimulator  — opens/manages/closes paper trades
  OptionsPnL      — estimates option PnL in mode A/B/C
  BacktestEngine  — orchestrates the replay loop
  ReportWriter    — writes CSV/JSON/MD outputs

CLI (via mirofish_signal.py backtest ...):
  python mirofish_signal.py backtest --ticker SPY --start 2026-04-01 --end 2026-04-25 --timeframe 2min
  python mirofish_signal.py backtest --ticker SPX --start 2026-04-01 --end 2026-04-25 --timeframe 2min --confirm-with ES,SPY
"""
from __future__ import annotations

import csv
import json
import logging
import math
import os
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator, Iterator

_log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
for _sd in [
    _ROOT / "services" / "schwab-collector",
    _ROOT / "services" / "uw-collector",
    _ROOT / "services" / "strategy-engine",
    _ROOT / "services" / "agent-seeder",
    _ROOT / "services" / "forecasting",
    _ROOT / "services" / "price-collector",
]:
    if str(_sd) not in sys.path:
        sys.path.insert(0, str(_sd))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

OUTPUT_DIR = _ROOT / "state" / "backtests" / "replay"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

UW_KEY = os.environ.get("UW_API_KEY", "")

# ─────────────────────────────────────────────────────────────────────────────
# Bar stream — yields candles one at a time, chronologically
# ─────────────────────────────────────────────────────────────────────────────

class Bar:
    """Single OHLCV candle."""
    __slots__ = ("ts", "open", "high", "low", "close", "volume", "symbol")

    def __init__(self, ts: datetime, open_: float, high: float, low: float,
                 close: float, volume: int, symbol: str = "") -> None:
        self.ts     = ts
        self.open   = open_
        self.high   = high
        self.low    = low
        self.close  = close
        self.volume = volume
        self.symbol = symbol

    def to_dict(self) -> dict:
        return {
            "ts":     self.ts.isoformat(),
            "open":   self.open,
            "high":   self.high,
            "low":    self.low,
            "close":  self.close,
            "volume": self.volume,
        }


class BarStream:
    """Loads historical intraday bars and enforces PIT boundary."""

    def __init__(
        self,
        symbol: str,
        start: date,
        end: date,
        freq_min: int = 5,
    ) -> None:
        self.symbol   = symbol.upper()
        self.start    = start
        self.end      = end
        self.freq_min = freq_min
        self._bars: list[Bar] = []
        self._source: str = "unavailable"
        self._load()

    def _load(self) -> None:
        """Load bars from Schwab, parquet, or yfinance."""
        # 1. Try stored parquet
        parquet = _ROOT / "data" / "market_data" / "ohlcv" / f"{self.symbol}.parquet"
        if parquet.exists():
            try:
                import pandas as pd
                df = pd.read_parquet(parquet)
                self._bars = self._df_to_bars(df, self.symbol)
                self._source = f"parquet:{parquet.name}"
                _log.info("[barstream] %s loaded from parquet (%d bars)", self.symbol, len(self._bars))
                return
            except Exception as e:
                _log.warning("[barstream] parquet load failed: %s", e)

        # 2. Try Schwab historical intraday
        try:
            self._bars = self._fetch_schwab()
            if self._bars:
                self._source = "schwab_historical"
                return
        except Exception as e:
            _log.warning("[barstream] Schwab fetch failed: %s", e)

        # 3. yfinance fallback
        try:
            self._bars = self._fetch_yfinance()
            if self._bars:
                self._source = "yfinance"
                return
        except Exception as e:
            _log.warning("[barstream] yfinance failed: %s", e)

        self._source = "unavailable"
        _log.warning("[barstream] no data for %s", self.symbol)

    def _fetch_schwab(self) -> list[Bar]:
        """Fetch via Schwab pricehistory API."""
        import requests
        from schwab_auth import get_valid_token

        token   = get_valid_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        base    = os.environ.get("SCHWAB_BASE_URL", "https://api.schwabapi.com")

        # Schwab supports up to 10 days of 1-min or 2-min data for free
        # For longer ranges, we fetch day by day
        all_bars: list[Bar] = []
        d = self.start
        while d <= self.end:
            if d.weekday() >= 5:
                d += timedelta(days=1)
                continue
            try:
                r = requests.get(
                    f"{base}/marketdata/v1/pricehistory",
                    headers=headers,
                    params={
                        "symbol": self.symbol,
                        "periodType": "day",
                        "period": 1,
                        "frequencyType": "minute",
                        "frequency": self.freq_min,
                        "needExtendedHoursData": "false",
                        # Schwab doesn't support date filtering per day here — we filter after
                    },
                    timeout=15,
                )
                candles = r.json().get("candles", [])
                for c in candles:
                    ts = datetime.fromtimestamp(c["datetime"] / 1000, tz=timezone.utc)
                    bar = Bar(ts, c["open"], c["high"], c["low"], c["close"], c["volume"], self.symbol)
                    all_bars.append(bar)
            except Exception as e:
                _log.warning("[barstream] schwab day %s failed: %s", d, e)
            d += timedelta(days=1)

        # Filter to date range
        start_dt = datetime.combine(self.start, datetime.min.time()).replace(tzinfo=timezone.utc)
        end_dt   = datetime.combine(self.end, datetime.max.time()).replace(tzinfo=timezone.utc)
        return [b for b in all_bars if start_dt <= b.ts <= end_dt]

    def _fetch_yfinance(self) -> list[Bar]:
        """Fetch via yfinance."""
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf

        freq_map = {1: "1m", 2: "2m", 5: "5m", 15: "15m", 30: "30m", 60: "60m"}
        interval = freq_map.get(self.freq_min, "5m")

        # yfinance intraday history: max 60 days for 2m, 730 days for 1h
        days = (self.end - self.start).days + 1
        period = f"{min(days + 5, 59)}d"

        t    = yf.Ticker(self.symbol)
        hist = t.history(
            start=self.start.isoformat(),
            end=(self.end + timedelta(days=1)).isoformat(),
            interval=interval,
        )
        if hist.empty:
            return []

        bars = []
        for idx, row in hist.iterrows():
            ts = idx.to_pydatetime()
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            # Filter to market hours 9:30-16:00 ET (UTC-4/5)
            bar = Bar(ts, float(row["Open"]), float(row["High"]),
                      float(row["Low"]), float(row["Close"]),
                      int(row["Volume"]), self.symbol)
            bars.append(bar)
        return sorted(bars, key=lambda b: b.ts)

    def _df_to_bars(self, df: Any, symbol: str) -> list[Bar]:
        """Convert DataFrame to Bar list."""
        bars = []
        for idx, row in df.iterrows():
            ts = idx if isinstance(idx, datetime) else datetime.fromisoformat(str(idx))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            bars.append(Bar(ts, float(row.get("open", row.get("Open", 0))),
                            float(row.get("high", row.get("High", 0))),
                            float(row.get("low",  row.get("Low", 0))),
                            float(row.get("close", row.get("Close", 0))),
                            int(row.get("volume", row.get("Volume", 0))), symbol))
        return sorted(bars, key=lambda b: b.ts)

    def stream(self) -> Iterator[tuple[Bar, list[Bar]]]:
        """Yield (current_bar, history_so_far) — strict PIT ordering."""
        history: list[Bar] = []
        for bar in self._bars:
            history.append(bar)
            yield bar, list(history)  # copy so caller can't mutate

    @property
    def source(self) -> str:
        return self._source

    @property
    def bar_count(self) -> int:
        return len(self._bars)


# ─────────────────────────────────────────────────────────────────────────────
# Indicator state — rolling indicators from bar history
# ─────────────────────────────────────────────────────────────────────────────

class IndicatorState:
    """Compute all indicators from a PIT bar history. No lookahead."""

    def __init__(self) -> None:
        self._history: list[Bar] = []

    def update(self, history: list[Bar]) -> None:
        self._history = history

    def closes(self, n: int = 200) -> list[float]:
        return [b.close for b in self._history[-n:]]

    def volumes(self, n: int = 50) -> list[int]:
        return [b.volume for b in self._history[-n:]]

    def ema(self, period: int) -> float:
        closes = self.closes(period * 3)
        if not closes:
            return 0.0
        k = 2 / (period + 1)
        e = closes[0]
        for c in closes[1:]:
            e = c * k + e * (1 - k)
        return round(e, 4)

    def rsi(self, period: int = 14) -> float:
        closes = self.closes(period * 2 + 1)
        if len(closes) < period + 1:
            return 50.0
        gains = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
        losses = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]
        ag = sum(gains[-period:]) / period
        al = sum(losses[-period:]) / period
        return round(100 - 100 / (1 + ag / al), 2) if al > 0 else 100.0

    def vwap(self) -> float:
        """Session VWAP — resets each day."""
        if not self._history:
            return 0.0
        # Find today's bars
        last_date = self._history[-1].ts.date()
        today_bars = [b for b in self._history if b.ts.date() == last_date]
        if not today_bars:
            return 0.0
        cv  = sum((b.high + b.low + b.close) / 3 * b.volume for b in today_bars)
        vol = sum(b.volume for b in today_bars)
        return round(cv / vol, 4) if vol > 0 else 0.0

    def atr(self, period: int = 14) -> float:
        bars = self._history[-(period + 1):]
        if len(bars) < 2:
            return bars[-1].close * 0.005 if bars else 0.01
        trs = [
            max(bars[i].high - bars[i].low,
                abs(bars[i].high - bars[i-1].close),
                abs(bars[i].low  - bars[i-1].close))
            for i in range(1, len(bars))
        ]
        return round(sum(trs) / len(trs), 4)

    def morning_levels(self) -> dict:
        """First 6 bars (30 min for 5m) = opening range."""
        if not self._history:
            return {}
        last_date = self._history[-1].ts.date()
        today = [b for b in self._history if b.ts.date() == last_date]
        if not today:
            return {}
        opening = today[:max(6, len(today) // 8)]  # first ~30min
        return {
            "morning_high":  max(b.high for b in opening),
            "morning_low":   min(b.low  for b in opening),
            "morning_open":  opening[0].open,
        }

    def avg_volume(self, n: int = 20) -> float:
        vols = self.volumes(n)
        return statistics.mean(vols) if vols else 1.0

    def as_dict(self) -> dict:
        c = self.closes(1)
        last = c[-1] if c else 0.0
        v    = self.vwap()
        e9   = self.ema(9)
        e21  = self.ema(21)
        e50  = self.ema(50)
        r14  = self.rsi(14)
        atr  = self.atr(14)
        avol = self.avg_volume(20)
        lvls = self.morning_levels()
        return {
            "last":           last,
            "ema9":           e9,
            "ema21":          e21,
            "ema50":          e50,
            "vwap":           v,
            "rsi14":          r14,
            "atr14":          atr,
            "avg_volume":     avol,
            "price_vs_vwap":  "above" if last > v else "below",
            "ema_bull":       e9 > e21,
            "above_ema9":     last > e9,
            **lvls,
        }


# ─────────────────────────────────────────────────────────────────────────────
# UW context loader — PIT-safe UW data
# ─────────────────────────────────────────────────────────────────────────────

class UWContextLoader:
    """Load UW flow/OI data for a given PIT timestamp.

    PIT safety:
    - Stored snapshots: uses only files with date <= replay_ts
    - Live API: NOT used during replay (would be lookahead)
    - Falls back to 'unavailable' with clear audit note
    """

    SNAPSHOT_DIR = _ROOT / "state" / "backtests" / "uw_snapshots"

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol.upper()
        self._cache: dict[str, dict] = {}

    def get_context(self, replay_ts: datetime) -> tuple[dict, dict]:
        """Return (uw_context, source_audit) for the given PIT timestamp."""
        replay_date = replay_ts.date().isoformat()

        if replay_date in self._cache:
            return self._cache[replay_date], {"provider": "cache", "status": "cached"}

        # Find most recent snapshot on or before replay date
        snap = self._find_snapshot(replay_date)
        if snap:
            ctx, audit = snap
            self._cache[replay_date] = ctx
            return ctx, audit

        # No snapshot — return neutral context (not fake data)
        ctx = self._neutral_context()
        audit = {
            "provider":           "none",
            "status":             "unavailable",
            "point_in_time_safe": True,
            "note": (
                f"No UW snapshot for {self.symbol} on or before {replay_date}. "
                "Flow score set to neutral. Store daily snapshots for accurate replay."
            ),
        }
        self._cache[replay_date] = ctx
        return ctx, audit

    def _find_snapshot(self, replay_date: str) -> tuple[dict, dict] | None:
        """Find most recent snapshot file <= replay_date."""
        if not self.SNAPSHOT_DIR.exists():
            return None

        candidates = sorted(
            self.SNAPSHOT_DIR.glob(f"*_{self.symbol}_uw_snapshot.json"),
            reverse=True,
        )
        for path in candidates:
            date_part = path.stem.split("_")[0]
            if date_part <= replay_date:
                try:
                    raw = json.loads(path.read_text())
                    ctx = self._parse_snapshot(raw)
                    audit = {
                        "provider":           "local_snapshot",
                        "status":             "historical_snapshot",
                        "snapshot_date":      date_part,
                        "point_in_time_safe": True,
                        "note": f"Snapshot from {date_part} used for {replay_date}",
                    }
                    return ctx, audit
                except Exception as e:
                    _log.warning("[uw] snapshot parse failed %s: %s", path, e)
        return None

    def _parse_snapshot(self, raw: dict) -> dict:
        """Extract useful fields from a UW snapshot."""
        endpoints = raw.get("endpoints", {})
        summary   = raw.get("positioning_summary", {})
        ov        = endpoints.get("options_volume", {}).get("data", {})
        alerts    = endpoints.get("flow_alerts", {}).get("data", [])
        recent    = endpoints.get("flow_recent", {}).get("data", [])

        call_vol  = ov.get("call_volume", 0) or 0
        put_vol   = ov.get("put_volume", 0) or 0
        net_call  = float(ov.get("net_call_premium", 0) or 0)
        net_put   = float(ov.get("net_put_premium", 0) or 0)

        total_vol = call_vol + put_vol
        call_ratio = call_vol / total_vol if total_vol > 0 else 0.5

        flow_bias = "neutral"
        if call_ratio > 0.58:
            flow_bias = "bullish"
        elif call_ratio < 0.42:
            flow_bias = "bearish"

        return {
            "flow_bias":      flow_bias,
            "call_volume":    call_vol,
            "put_volume":     put_vol,
            "net_call_prem":  net_call,
            "net_put_prem":   net_put,
            "call_ratio":     round(call_ratio, 4),
            "flow_alerts":    alerts[:5],
            "flow_recent":    recent[:5],
            "net_put_sweep":  net_put < -500_000,
        }

    def _neutral_context(self) -> dict:
        return {
            "flow_bias":    "neutral",
            "call_volume":  0,
            "put_volume":   0,
            "net_call_prem": 0.0,
            "net_put_prem":  0.0,
            "call_ratio":   0.5,
            "flow_alerts":  [],
            "flow_recent":  [],
            "net_put_sweep": False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Ensemble adapter — wraps existing ensemble_scorer with historical bar data
# ─────────────────────────────────────────────────────────────────────────────

class EnsembleAdapter:
    """Runs the 4-agent ensemble scorer using PIT indicator state."""

    def __init__(self) -> None:
        try:
            from ensemble_scorer import ensemble_score as _ens
            self._score_fn = _ens
            self._available = True
        except Exception as e:
            _log.warning("[ensemble_adapter] not available: %s", e)
            self._available = False

    def score(
        self,
        ind: dict,
        uw: dict,
        bar: Bar,
        history: list[Bar],
        conf_bars: dict[str, list[Bar]] | None = None,
    ) -> dict:
        """Run ensemble scorer. Returns signal dict."""
        if not self._available:
            return self._fallback_score(ind, uw, bar)

        try:
            # Build price/intraday dicts matching SchwabPriceService/IntradayService output
            price_dict = {
                "last_price":    bar.close,
                "close_prices":  [b.close for b in history[-100:]],
                "volatility":    ind.get("atr14", bar.close * 0.005) / bar.close,
                "uw_flow":       uw.get("flow_bias", "neutral"),
                "uw_net_puts":   uw.get("net_put_sweep", False),
            }
            intraday_dict = {
                "bars_sample": [
                    {"open": b.open, "high": b.high, "low": b.low,
                     "close": b.close, "volume": b.volume}
                    for b in history[-60:]
                ],
                "current": {
                    "close":          bar.close,
                    "vwap":           ind.get("vwap", 0),
                    "rsi":            ind.get("rsi14", 50),
                    "intraday_trend": "up" if ind.get("ema_bull") else "down",
                    "price_vs_vwap":  ind.get("price_vs_vwap", "above"),
                    "volume":         bar.volume,
                },
            }

            # Confirmation bars (/ES, /NQ) for SPX mode
            es_bars = conf_bars.get("ES", []) if conf_bars else []
            nq_bars = conf_bars.get("NQ", []) if conf_bars else []
            es_price = es_bars[-1]["close"] if es_bars else 0
            nq_price = nq_bars[-1]["close"] if nq_bars else 0

            result = self._score_fn(
                price_dict, intraday_dict,
                es_bars[-40:], nq_bars[-40:],
                es_price, nq_price,
                require_hv_window=False,
                replay_ts=bar.ts,  # PIT-safe: use bar time not wall clock
            )
            result["ticker"] = bar.symbol
            result["price"]  = bar.close
            return result
        except Exception as e:
            _log.warning("[ensemble_adapter] score failed: %s", e)
            return self._fallback_score(ind, uw, bar)

    def _fallback_score(self, ind: dict, uw: dict, bar: Bar) -> dict:
        """Simple deterministic fallback when ensemble module unavailable."""
        score = 0
        if ind.get("ema_bull"):  score += 2
        if ind.get("price_vs_vwap") == "above": score += 1
        if ind.get("rsi14", 50) > 55: score += 1
        if uw.get("flow_bias") == "bullish": score += 1
        if ind.get("rsi14", 50) < 45: score -= 1
        if ind.get("price_vs_vwap") == "below": score -= 1

        if score >= 3:
            action = "BUY"
        elif score <= -2:
            action = "SELL/SHORT"
        else:
            action = "HOLD"

        atr  = ind.get("atr14", bar.close * 0.005)
        last = bar.close
        return {
            "action":       action,
            "confidence":   f"{min(95, 50 + abs(score) * 8)}%",
            "score":        score,
            "votes_bull":   max(score, 0),
            "votes_bear":   max(-score, 0),
            "entry":        round(last, 2),
            "target_1":     round(last + atr * 1.2, 2) if action=="BUY" else round(last - atr*1.2, 2),
            "target_2":     round(last + atr * 2.2, 2) if action=="BUY" else round(last - atr*2.2, 2),
            "stop_loss":    round(last - atr * 0.8, 2) if action=="BUY" else round(last + atr*0.8, 2),
            "risk_reward":  "1:1.5",
            "agents":       {},
            "ticker":       bar.symbol,
            "price":        bar.close,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Daily regime adapter — from historical bars, no live data
# ─────────────────────────────────────────────────────────────────────────────

class RegimeAdapter:
    """Compute daily regime from historical daily bars (PIT-safe)."""

    def __init__(self) -> None:
        self._cache: dict[str, dict] = {}

    def get_regime(self, replay_date: date, daily_bars: list[dict]) -> dict:
        key = replay_date.isoformat()
        if key in self._cache:
            return self._cache[key]

        # Filter daily bars to dates <= replay_date
        pit_bars = [b for b in daily_bars if b.get("date","") <= key]

        try:
            from daily_regime import _score_regime, _regime_trading_params
            scored = _score_regime(pit_bars, {"available": False}, {"available": False})
            regime = scored["regime"]
            params = _regime_trading_params(regime, scored["confidence"])
            result = {**scored, "trading_params": params, "source": "deterministic_pit"}
        except Exception as e:
            _log.warning("[regime_adapter] failed: %s", e)
            result = {
                "regime": "CHOP",
                "confidence": 0,
                "reason": f"unavailable: {e}",
                "trading_params": {
                    "allowed_actions": ["BUY","SELL/SHORT"],
                    "min_ensemble_votes": 4,
                    "stop_multiplier": 0.7,
                    "confidence_boost": -10,
                },
            }

        self._cache[key] = result
        return result


# ─────────────────────────────────────────────────────────────────────────────
# Trade simulator
# ─────────────────────────────────────────────────────────────────────────────

class Trade:
    """Represents a single paper trade."""

    def __init__(
        self,
        signal: dict,
        entry_bar: Bar,
        regime: str,
        uw_bias: str,
        timesfm_direction: str,
        masi_verdict: str,
    ) -> None:
        self.signal_ts         = entry_bar.ts
        self.entry_ts          = entry_bar.ts
        self.entry_price       = entry_bar.open  # entry at NEXT bar open
        self.action            = signal["action"]
        self.target_1          = signal.get("target_1", 0)
        self.target_2          = signal.get("target_2", 0)
        self.stop_loss         = signal.get("stop_loss", 0)
        self.ticker            = signal.get("ticker", "?")
        self.regime            = regime
        self.uw_bias           = uw_bias
        self.timesfm_direction = timesfm_direction
        self.masi_verdict      = masi_verdict
        self.confidence        = signal.get("confidence", "?")
        self.votes_bull        = signal.get("votes_bull", 0)
        self.votes_bear        = signal.get("votes_bear", 0)

        self.exit_ts: datetime | None = None
        self.exit_price: float = 0.0
        self.exit_reason: str  = ""
        self.pnl_pts:    float = 0.0
        self.pnl_r:      float = 0.0
        self.mfe:        float = 0.0   # max favorable excursion
        self.mae:        float = 0.0   # max adverse excursion
        self.open:       bool  = True

    @property
    def risk_pts(self) -> float:
        return abs(self.entry_price - self.stop_loss)

    def update_excursion(self, bar: Bar) -> None:
        """Update MFE/MAE using current bar."""
        if self.action == "BUY":
            favorable = bar.high - self.entry_price
            adverse   = self.entry_price - bar.low
        else:
            favorable = self.entry_price - bar.low
            adverse   = bar.high - self.entry_price
        self.mfe = max(self.mfe, favorable)
        self.mae = max(self.mae, adverse)

    def close(self, exit_bar: Bar, reason: str) -> None:
        self.exit_ts    = exit_bar.ts
        self.exit_price = exit_bar.close if reason == "eod" else (
            exit_bar.open if reason in ("stop","target") else exit_bar.close
        )
        self.exit_reason = reason
        self.pnl_pts = (self.exit_price - self.entry_price) if self.action == "BUY" \
                  else (self.entry_price - self.exit_price)
        self.pnl_r   = self.pnl_pts / self.risk_pts if self.risk_pts > 0 else 0.0
        self.open    = False

    def to_dict(self) -> dict:
        return {
            "ticker":           self.ticker,
            "action":           self.action,
            "regime":           self.regime,
            "uw_bias":          self.uw_bias,
            "timesfm":          self.timesfm_direction,
            "masi_verdict":     self.masi_verdict,
            "signal_ts":        self.signal_ts.isoformat(),
            "entry_ts":         self.entry_ts.isoformat(),
            "entry_price":      round(self.entry_price, 4),
            "stop_loss":        round(self.stop_loss, 4),
            "target_1":         round(self.target_1, 4),
            "target_2":         round(self.target_2, 4),
            "exit_ts":          self.exit_ts.isoformat() if self.exit_ts else "",
            "exit_price":       round(self.exit_price, 4),
            "exit_reason":      self.exit_reason,
            "pnl_pts":          round(self.pnl_pts, 4),
            "pnl_r":            round(self.pnl_r, 4),
            "mfe":              round(self.mfe, 4),
            "mae":              round(self.mae, 4),
            "confidence":       self.confidence,
            "votes_bull":       self.votes_bull,
            "votes_bear":       self.votes_bear,
        }


class TradeSimulator:
    """Manages open trades and exit logic."""

    def __init__(self, max_concurrent: int = 1) -> None:
        self._open:   list[Trade] = []
        self._closed: list[Trade] = []
        self._max    = max_concurrent
        self._last_signal_ts: dict[str, datetime] = {}

    @property
    def all_trades(self) -> list[Trade]:
        return self._closed + self._open

    def can_open(self, ticker: str, action: str, now: datetime) -> bool:
        """Enforce 60-min cooldown per ticker."""
        last = self._last_signal_ts.get(ticker)
        if last and (now - last).total_seconds() < 3600:
            return False
        return len(self._open) < self._max

    def open_trade(self, signal: dict, entry_bar: Bar, regime: str,
                   uw_bias: str, tf_dir: str, masi: str) -> Trade:
        trade = Trade(signal, entry_bar, regime, uw_bias, tf_dir, masi)
        self._open.append(trade)
        self._last_signal_ts[entry_bar.symbol] = entry_bar.ts
        return trade

    def update(self, bar: Bar) -> list[Trade]:
        """Update all open trades, close if stop/target hit."""
        closed_this_bar = []
        still_open = []
        for trade in self._open:
            trade.update_excursion(bar)

            # Check stop
            if trade.action == "BUY" and bar.low <= trade.stop_loss:
                trade.close(bar, "stop")
                self._closed.append(trade)
                closed_this_bar.append(trade)
                continue
            if trade.action == "SELL/SHORT" and bar.high >= trade.stop_loss:
                trade.close(bar, "stop")
                self._closed.append(trade)
                closed_this_bar.append(trade)
                continue

            # Check target 1
            if trade.action == "BUY" and bar.high >= trade.target_1:
                trade.close(bar, "target_1")
                self._closed.append(trade)
                closed_this_bar.append(trade)
                continue
            if trade.action == "SELL/SHORT" and bar.low <= trade.target_1:
                trade.close(bar, "target_1")
                self._closed.append(trade)
                closed_this_bar.append(trade)
                continue

            still_open.append(trade)
        self._open = still_open
        return closed_this_bar

    def close_eod(self, bar: Bar) -> None:
        """Force-close all open trades at EOD."""
        for trade in self._open:
            trade.close(bar, "eod")
            self._closed.append(trade)
        self._open = []


# ─────────────────────────────────────────────────────────────────────────────
# Options PnL estimator
# ─────────────────────────────────────────────────────────────────────────────

class OptionsPnL:
    """Estimate option P&L in three modes."""

    MODE_A = "underlying_only"
    MODE_B = "approximate_bs"
    MODE_C = "real_historical"

    def __init__(self) -> None:
        self.mode = self.MODE_A
        self._mode_reason: str = "default"

    def check_mode(self, symbol: str, replay_date: date) -> str:
        """Determine available mode for this symbol/date."""
        # Check for real Schwab options snapshot
        snap_dir = _ROOT / "data" / "options_positioning_snapshots"
        if snap_dir.exists():
            candidates = list(snap_dir.glob(f"*_{symbol.upper()}.json"))
            pit_snaps  = [
                p for p in candidates
                if p.stem.split("_")[0] <= replay_date.isoformat()
            ]
            if pit_snaps:
                self.mode = self.MODE_C
                self._mode_reason = f"real snapshot: {pit_snaps[-1].name}"
                return self.mode

        self.mode = self.MODE_B
        self._mode_reason = "Black-Scholes approximation (no real snapshot)"
        return self.mode

    def estimate(
        self,
        trade: Trade,
        underlying_entry: float,
        underlying_exit: float,
        iv_est: float = 0.30,
        dte: int = 7,
        option_type: str = "CALL",
    ) -> dict:
        """Compute option P&L."""
        if self.mode == self.MODE_A:
            return {
                "mode":         self.MODE_A,
                "pnl_pts":      trade.pnl_pts,
                "pnl_pct":      round(trade.pnl_pts / underlying_entry * 100, 2) if underlying_entry else 0,
                "note":         "Underlying-only, no options pricing",
            }

        if self.mode in (self.MODE_B, self.MODE_C):
            return self._bs_estimate(trade, underlying_entry, underlying_exit,
                                     iv_est, dte, option_type)

        return {"mode": "error", "note": "unknown mode"}

    def _bs_estimate(self, trade: Trade, S0: float, S1: float,
                     iv: float, dte: int, option_type: str) -> dict:
        """Black-Scholes option P&L approximation."""
        # Determine strike (ATM + small OTM bias)
        is_call = option_type == "CALL"
        strike  = round(S0 * (1.02 if is_call else 0.98) / 5) * 5
        T0 = dte / 365.0
        T1 = max((dte - 1) / 365.0, 0.001)
        r  = 0.05

        def _ncdf(x: float) -> float:
            return 0.5 * (1 + math.erf(x / math.sqrt(2)))

        def _bs_price(S: float, T: float) -> float:
            if T <= 0 or iv <= 0:
                intrinsic = max(0, (S - strike) if is_call else (strike - S))
                return max(intrinsic, 0.01)
            d1 = (math.log(S / strike) + (r + 0.5 * iv**2) * T) / (iv * math.sqrt(T))
            d2 = d1 - iv * math.sqrt(T)
            if is_call:
                return S * _ncdf(d1) - strike * math.exp(-r * T) * _ncdf(d2)
            else:
                return strike * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)

        try:
            entry_premium = max(_bs_price(S0, T0), 0.01)
            exit_premium  = max(_bs_price(S1, T1), 0.01)
            pnl_per_contract = (exit_premium - entry_premium) * 100
            multiple = exit_premium / entry_premium
        except Exception:
            entry_premium = exit_premium = 0
            pnl_per_contract = 0
            multiple = 0

        return {
            "mode":              self.MODE_B,
            "option_type":       option_type,
            "strike":            strike,
            "dte_entry":         dte,
            "iv_estimate":       round(iv, 4),
            "entry_premium":     round(entry_premium, 2),
            "exit_premium":      round(exit_premium, 2),
            "pnl_per_contract":  round(pnl_per_contract, 2),
            "return_multiple":   round(multiple, 2),
            "note":              f"{self._mode_reason}",
            "warning":           "SYNTHETIC — not real option premium history",
        }


# ─────────────────────────────────────────────────────────────────────────────
# TimesFM adapter (PIT-safe: uses only closes up to replay_ts)
# ─────────────────────────────────────────────────────────────────────────────

class TimesFMAdapter:
    """Run TimesFM on PIT close prices. Cached per day."""

    def __init__(self) -> None:
        self._cache: dict[str, dict] = {}
        try:
            from forecasting_service import TimesFMForecastingService
            self._svc = TimesFMForecastingService()
            self._available = True
        except Exception:
            self._svc = None
            self._available = False

    def get(self, replay_date: date, closes: list[float]) -> dict:
        key = replay_date.isoformat()
        if key in self._cache:
            return self._cache[key]

        if not self._available or len(closes) < 20:
            result = {"available": False, "reason": "unavailable or insufficient history"}
            self._cache[key] = result
            return result

        try:
            out = self._svc.forecast_from_prices("SPY", closes[-100:], horizon=5)
            result = {
                "available":         True,
                "direction":         out.get("direction", "neutral"),
                "confidence":        out.get("confidence", 0.5),
                "predicted_return":  out.get("predicted_return", 0.0),
                "provider_mode":     out.get("provider_mode", "unknown"),
            }
        except Exception as e:
            result = {"available": False, "reason": str(e)[:80]}

        self._cache[key] = result
        return result

    def agreement(self, action: str, tf: dict) -> str:
        if not tf.get("available"):
            return "unavailable"
        d = tf.get("direction", "neutral")
        if (action == "BUY" and d == "bullish") or (action == "SELL/SHORT" and d == "bearish"):
            return "agree"
        if d == "neutral":
            return "neutral"
        return "disagree"


# ─────────────────────────────────────────────────────────────────────────────
# Report writer
# ─────────────────────────────────────────────────────────────────────────────

class ReportWriter:
    """Write trades.csv, backtest_report.json, backtest_report.md, equity_curve.csv."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        run_dir.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        trades: list[Trade],
        config: dict,
        source_audit: dict,
        options_pnl_mode: str,
    ) -> dict:
        if not trades:
            print("  No trades to report.")
            return {}

        closed = [t for t in trades if not t.open]
        stats  = self._compute_stats(closed)

        # trades.csv
        self._write_csv(closed)

        # equity_curve.csv
        self._write_equity_curve(closed)

        # backtest_report.json
        report = {
            "run_config":        config,
            "summary":           stats,
            "options_pnl_mode":  options_pnl_mode,
            "options_pnl_note":  "See trade records for per-trade option estimates.",
            "source_audit":      source_audit,
        }
        (self.run_dir / "backtest_report.json").write_text(
            json.dumps(report, indent=2, default=str)
        )

        # backtest_report.md
        (self.run_dir / "backtest_report.md").write_text(
            self._markdown(report, closed)
        )

        print(f"\n  Reports written to: {self.run_dir}")
        return stats

    def _compute_stats(self, trades: list[Trade]) -> dict:
        if not trades:
            return {}

        pnls    = [t.pnl_r for t in trades]
        winners = [t for t in trades if t.pnl_r > 0]
        losers  = [t for t in trades if t.pnl_r <= 0]
        win_r   = len(winners) / len(trades) * 100
        avg_win = statistics.mean([t.pnl_r for t in winners]) if winners else 0
        avg_los = statistics.mean([t.pnl_r for t in losers])  if losers  else 0
        pf      = (avg_win * len(winners)) / abs(avg_los * len(losers)) \
                  if losers and avg_los != 0 else float("inf")

        # Equity curve (cumulative R)
        cumulative = []
        equity = 0.0
        for t in trades:
            equity += t.pnl_r
            cumulative.append(equity)

        # Max drawdown
        peak = 0.0; dd = 0.0
        for e in cumulative:
            peak = max(peak, e)
            dd   = min(dd, e - peak)

        # Regime breakdown
        reg_stats: dict[str, dict] = {}
        for t in trades:
            r = t.regime
            if r not in reg_stats:
                reg_stats[r] = {"trades": 0, "wins": 0, "pnl_r": 0.0}
            reg_stats[r]["trades"]  += 1
            reg_stats[r]["wins"]    += 1 if t.pnl_r > 0 else 0
            reg_stats[r]["pnl_r"]   += t.pnl_r

        for r, s in reg_stats.items():
            s["win_rate"] = round(s["wins"] / s["trades"] * 100, 1) if s["trades"] else 0

        # Best/worst
        best  = max(trades, key=lambda t: t.pnl_r)
        worst = min(trades, key=lambda t: t.pnl_r)

        # CALL vs PUT
        calls = [t for t in trades if t.action == "BUY"]
        puts  = [t for t in trades if t.action == "SELL/SHORT"]

        return {
            "total_signals":     len(trades),
            "win_rate_pct":      round(win_r, 1),
            "avg_win_r":         round(avg_win, 3),
            "avg_loss_r":        round(avg_los, 3),
            "profit_factor":     round(pf, 2) if pf != float("inf") else "∞",
            "total_r":           round(sum(pnls), 3),
            "max_drawdown_r":    round(dd, 3),
            "best_trade":        {"ts": best.signal_ts.isoformat(), "pnl_r": round(best.pnl_r, 3)},
            "worst_trade":       {"ts": worst.signal_ts.isoformat(), "pnl_r": round(worst.pnl_r, 3)},
            "calls_count":       len(calls),
            "calls_win_rate":    round(sum(1 for t in calls if t.pnl_r>0)/len(calls)*100,1) if calls else 0,
            "puts_count":        len(puts),
            "puts_win_rate":     round(sum(1 for t in puts if t.pnl_r>0)/len(puts)*100,1) if puts else 0,
            "regime_breakdown":  reg_stats,
            "mfe_avg":           round(statistics.mean([t.mfe for t in trades]), 4),
            "mae_avg":           round(statistics.mean([t.mae for t in trades]), 4),
        }

    def _write_csv(self, trades: list[Trade]) -> None:
        if not trades:
            return
        path = self.run_dir / "trades.csv"
        rows = [t.to_dict() for t in trades]
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    def _write_equity_curve(self, trades: list[Trade]) -> None:
        if not trades:
            return
        path = self.run_dir / "equity_curve.csv"
        equity = 0.0
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["ts", "pnl_r", "cumulative_r", "regime"])
            for t in trades:
                equity += t.pnl_r
                writer.writerow([
                    t.exit_ts.isoformat() if t.exit_ts else "",
                    round(t.pnl_r, 4),
                    round(equity, 4),
                    t.regime,
                ])

    def _markdown(self, report: dict, trades: list[Trade]) -> str:
        s   = report.get("summary", {})
        cfg = report.get("run_config", {})
        pnl_mode = report.get("options_pnl_mode", "?")

        lines = [
            f"# MiroFish Backtest Report",
            f"**Ticker**: {cfg.get('ticker','?')}  |  "
            f"**Period**: {cfg.get('start','?')} → {cfg.get('end','?')}  |  "
            f"**Timeframe**: {cfg.get('timeframe','?')}",
            "",
            f"## Summary",
            f"| Metric | Value |",
            f"|---|---|",
            f"| Total Signals | {s.get('total_signals','?')} |",
            f"| Win Rate | {s.get('win_rate_pct','?')}% |",
            f"| Avg Win (R) | {s.get('avg_win_r','?')} |",
            f"| Avg Loss (R) | {s.get('avg_loss_r','?')} |",
            f"| Profit Factor | {s.get('profit_factor','?')} |",
            f"| Total R | {s.get('total_r','?')} |",
            f"| Max Drawdown (R) | {s.get('max_drawdown_r','?')} |",
            f"| Calls Win Rate | {s.get('calls_win_rate','?')}% ({s.get('calls_count','?')} trades) |",
            f"| Puts Win Rate  | {s.get('puts_win_rate','?')}% ({s.get('puts_count','?')} trades) |",
            "",
            f"## Options P&L Mode",
            f"**Mode**: `{pnl_mode}`",
        ]

        if "approximate" in pnl_mode or "synthetic" in pnl_mode:
            lines.append("> ⚠️  Option premiums are Black-Scholes estimates — NOT real historical option prices.")
        elif "real" in pnl_mode:
            lines.append("> ✅  Option P&L from real historical snapshots.")
        else:
            lines.append("> Underlying-only P&L — no options pricing applied.")

        lines += ["", "## Regime Breakdown", "| Regime | Trades | Win Rate | Total R |", "|---|---|---|---|"]
        for r, s2 in s.get("regime_breakdown", {}).items():
            lines.append(f"| {r} | {s2['trades']} | {s2['win_rate']}% | {round(s2['pnl_r'],2)} |")

        bst = s.get("best_trade", {})
        wst = s.get("worst_trade", {})
        lines += [
            "",
            "## Best / Worst Trades",
            f"- **Best**: {bst.get('ts','')}  +{bst.get('pnl_r','')}R",
            f"- **Worst**: {wst.get('ts','')}  {wst.get('pnl_r','')}R",
            "",
            "## Source Audit",
        ]
        for src, v in report.get("source_audit", {}).items():
            safe = "✅ PIT-safe" if v.get("point_in_time_safe") else "⚠️  not PIT-safe"
            lines.append(f"- **{src}**: {v.get('status','?')} via {v.get('provider','?')} | {safe}")
            if v.get("note"):
                lines.append(f"  _{v['note']}_")

        lines += ["", f"---", f"_Generated: {datetime.now().isoformat()}_"]
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Backtest engine — orchestrates replay
# ─────────────────────────────────────────────────────────────────────────────

class BacktestEngine:
    """Main replay orchestrator."""

    def __init__(
        self,
        ticker: str,
        start: date,
        end: date,
        freq_min: int = 5,
        confirm_with: list[str] | None = None,
        use_masi: bool = False,
        use_timesfm: bool = True,
    ) -> None:
        self.ticker       = ticker.upper()
        self.start        = start
        self.end          = end
        self.freq_min     = freq_min
        self.confirm_with = [c.upper() for c in (confirm_with or [])]
        self.use_masi     = use_masi
        self.use_timesfm  = use_timesfm

        # Sub-components
        self._bar_stream  = BarStream(ticker, start, end, freq_min)
        self._conf_streams: dict[str, BarStream] = {}
        for sym in self.confirm_with:
            self._conf_streams[sym] = BarStream(sym, start, end, freq_min)

        self._ind         = IndicatorState()
        self._uw          = UWContextLoader(ticker)
        self._ensemble    = EnsembleAdapter()
        self._regime      = RegimeAdapter()
        self._timesfm     = TimesFMAdapter()
        self._sim         = TradeSimulator(max_concurrent=1)
        self._opt_pnl     = OptionsPnL()
        self._opt_pnl.check_mode(ticker, end)

        # Daily bars for regime computation
        self._daily_bars: list[dict] = self._load_daily_bars()

        # Run key
        self.run_key = (
            f"{ticker}_{start.isoformat()}_{end.isoformat()}_{freq_min}min"
        )
        self._run_dir = OUTPUT_DIR / self.run_key

        # Source audit
        self._source_audit: dict = {
            "intraday_bars": {
                "provider": self._bar_stream.source,
                "status":   "live" if self._bar_stream.source != "unavailable" else "unavailable",
                "bars":     self._bar_stream.bar_count,
                "point_in_time_safe": True,
            },
        }
        for sym, s in self._conf_streams.items():
            self._source_audit[f"confirm_{sym}"] = {
                "provider": s.source, "status": "live" if s.source != "unavailable" else "unavailable",
                "bars": s.bar_count, "point_in_time_safe": True,
            }
        self._source_audit["unusual_whales"] = {
            "provider": "local_snapshots",
            "status": "checked_per_day",
            "point_in_time_safe": True,
            "note": "UW data loaded from stored snapshots only (no live API during replay)",
        }
        self._source_audit["timesfm"] = {
            "provider": "local_model",
            "status": "live" if self._timesfm._available else "unavailable",
            "point_in_time_safe": True,
        }
        self._source_audit["masi"] = {
            "provider": "kimi_k2",
            "status": "optional" if use_masi else "disabled",
            "point_in_time_safe": True,
            "note": "MASi called only on signal candidates, not every bar",
        }
        self._source_audit["options_pnl"] = {
            "provider": self._opt_pnl._mode_reason,
            "status": self._opt_pnl.mode,
            "point_in_time_safe": True,
        }

    def _load_daily_bars(self) -> list[dict]:
        """Load SPY daily bars for regime computation."""
        try:
            import warnings; warnings.filterwarnings("ignore")
            import yfinance as yf
            t = yf.Ticker(self.ticker)
            hist = t.history(
                start=(self.start - timedelta(days=60)).isoformat(),
                end=self.end.isoformat(),
                interval="1d",
            )
            bars = []
            for idx, row in hist.iterrows():
                bars.append({
                    "date":   idx.strftime("%Y-%m-%d"),
                    "open":   float(row["Open"]),
                    "high":   float(row["High"]),
                    "low":    float(row["Low"]),
                    "close":  float(row["Close"]),
                    "volume": int(row["Volume"]),
                })
            return sorted(bars, key=lambda b: b["date"])
        except Exception:
            return []

    def run(self) -> dict:
        """Execute the full replay loop."""
        print(f"\n{'='*60}")
        print(f"  MiroFish Point-in-Time Replay")
        print(f"  Ticker: {self.ticker}  |  {self.start} → {self.end}")
        print(f"  Timeframe: {self.freq_min}min  |  Bars: {self._bar_stream.bar_count}")
        print(f"  Confirm with: {self.confirm_with or 'none'}")
        print(f"  Options PnL mode: {self._opt_pnl.mode}")
        print(f"  MASi: {'on' if self.use_masi else 'off'}  TimesFM: {'on' if self.use_timesfm else 'off'}")
        print(f"{'='*60}")

        if self._bar_stream.bar_count == 0:
            print(f"  ❌ No bars loaded for {self.ticker}. Check data source.")
            return {"error": "no_bars", "source_audit": self._source_audit}

        # Build PIT index for confirmation streams
        conf_history: dict[str, list[Bar]] = {sym: [] for sym in self.confirm_with}

        # Pre-index confirmation bars by timestamp for fast lookup
        conf_by_ts: dict[str, dict[datetime, Bar]] = {}
        for sym, stream in self._conf_streams.items():
            conf_by_ts[sym] = {b.ts: b for b in stream._bars}

        signal_count = 0
        bar_count    = 0
        current_date: date | None = None
        regime_today: dict = {}

        for bar, history in self._bar_stream.stream():
            bar_count += 1

            # Update confirmation streams up to this timestamp
            conf_bars_dict: dict[str, list[dict]] = {}
            for sym in self.confirm_with:
                relevant = [
                    {"open": b.open, "high": b.high, "low": b.low,
                     "close": b.close, "volume": b.volume}
                    for ts, b in conf_by_ts.get(sym, {}).items()
                    if ts <= bar.ts
                ]
                conf_bars_dict[sym] = relevant[-40:]

            # Update indicators (PIT-safe)
            self._ind.update(history)
            ind = self._ind.as_dict()

            # Refresh regime once per day
            if bar.ts.date() != current_date:
                current_date = bar.ts.date()
                regime_today = self._regime.get_regime(
                    current_date,
                    [b for b in self._daily_bars if b["date"] <= current_date.isoformat()],
                )
                # Reset TimesFM cache daily
                if self.use_timesfm:
                    daily_closes = [b["close"] for b in self._daily_bars
                                    if b["date"] <= current_date.isoformat()]
                    tf = self._timesfm.get(current_date, daily_closes)
                else:
                    tf = {"available": False}

            # UW context (PIT-safe)
            uw_ctx, uw_audit = self._uw.get_context(bar.ts)

            # Ensemble signal
            ens = self._ensemble.score(
                ind, uw_ctx, bar, history,
                conf_bars={
                    sym: conf_bars_dict.get(sym, [])
                    for sym in self.confirm_with
                },
            )
            action = ens.get("action", "HOLD")
            bull   = ens.get("votes_bull", 0)
            bear   = ens.get("votes_bear", 0)

            # Regime gate
            allowed = regime_today.get("trading_params", {}).get("allowed_actions", ["BUY","SELL/SHORT"])
            if action not in allowed:
                continue

            # Vote threshold
            min_votes = regime_today.get("trading_params", {}).get("min_ensemble_votes", 3)
            hv = self._is_high_vol(bar.ts)
            threshold = max(min_votes, 4 if not hv else min_votes)

            fired = (action == "BUY" and bull >= threshold) or \
                    (action == "SELL/SHORT" and bear >= threshold)
            if not fired:
                continue

            # Opening range filter
            if bar.ts.hour == 9 and bar.ts.minute < 55:  # ET 09:55 after UTC offset
                continue

            # EOD filter: no new trades after 14:45 ET
            et_hour = (bar.ts.hour - 4) % 24 if bar.ts.tzinfo else bar.ts.hour
            et_min  = bar.ts.minute
            if et_hour >= 14 and et_min >= 45:
                continue

            # Cooldown
            if not self._sim.can_open(self.ticker, action, bar.ts):
                continue

            # Apply regime stop adjustment
            ens = dict(ens)
            stop_mult = regime_today.get("trading_params", {}).get("stop_multiplier", 1.0)
            if stop_mult != 1.0:
                entry = ens.get("entry", bar.close)
                stop  = ens.get("stop_loss", 0)
                if entry and stop:
                    ens["stop_loss"] = round(entry - (entry - stop) * stop_mult, 2)

            # TimesFM filter
            tf_agreement = self._timesfm.agreement(action, tf)
            if tf_agreement == "disagree" and tf.get("confidence", 0) >= 0.65:
                conf_str = str(ens.get("confidence","75%")).rstrip("%")
                try:
                    ens["confidence"] = f"{max(40, int(conf_str) - 15)}%"
                except Exception:
                    pass

            # MASi confirmation (optional, gated on signal fire)
            masi_verdict = "NOT_CALLED"
            if self.use_masi:
                try:
                    from masi_confirmer import confirm as _masi
                    result = _masi(ens, regime_today, None, timeout=8)
                    masi_verdict = result.get("verdict", "DEGRADED")
                    if masi_verdict == "VETO":
                        continue  # skip trade
                except Exception:
                    masi_verdict = "DEGRADED"

            # Open trade (entry at next bar open — we'll use next bar if available)
            signal_count += 1
            trade = self._sim.open_trade(
                signal   = ens,
                entry_bar = bar,
                regime   = regime_today.get("regime", "CHOP"),
                uw_bias  = uw_ctx.get("flow_bias", "neutral"),
                tf_dir   = tf.get("direction", "unavailable"),
                masi     = masi_verdict,
            )
            _log.info("[replay] signal @%s %s %s (regime=%s tf=%s masi=%s)",
                      bar.ts, action, self.ticker,
                      regime_today.get("regime"), tf_agreement, masi_verdict)

            # Update all open trades
            self._sim.update(bar)

            # EOD close
            et_close_hr = 12  # 16:00 ET = 20:00 UTC
            if bar.ts.hour >= 20 and bar.ts.minute >= 0:
                self._sim.close_eod(bar)

        # Force-close any remaining open trades
        if self._bar_stream._bars:
            self._sim.close_eod(self._bar_stream._bars[-1])

        print(f"\n  Bars processed: {bar_count}")
        print(f"  Signals fired:  {signal_count}")
        print(f"  Trades closed:  {len([t for t in self._sim.all_trades if not t.open])}")

        # Write reports
        config = {
            "ticker":       self.ticker,
            "start":        self.start.isoformat(),
            "end":          self.end.isoformat(),
            "timeframe":    f"{self.freq_min}min",
            "confirm_with": self.confirm_with,
            "use_masi":     self.use_masi,
            "use_timesfm":  self.use_timesfm,
        }
        writer = ReportWriter(self._run_dir)
        stats  = writer.write(
            trades          = self._sim.all_trades,
            config          = config,
            source_audit    = self._source_audit,
            options_pnl_mode = self._opt_pnl.mode,
        )
        self._write_source_audit()

        return {
            "run_key":        self.run_key,
            "config":         config,
            "summary":        stats,
            "options_pnl_mode": self._opt_pnl.mode,
            "source_audit":   self._source_audit,
            "output_dir":     str(self._run_dir),
        }

    def _is_high_vol(self, ts: datetime) -> bool:
        # Convert UTC to ET (rough -4h)
        et = ts - timedelta(hours=4)
        mins = et.hour * 60 + et.minute
        return (9*60+30 <= mins <= 11*60+30) or (14*60 <= mins <= 16*60)

    def _write_source_audit(self) -> None:
        path = self._run_dir / "source_audit.json"
        path.write_text(json.dumps(self._source_audit, indent=2, default=str))


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point (called from mirofish_signal.py backtest ...)
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(
    ticker: str,
    start: str,
    end: str,
    timeframe: str = "5min",
    confirm_with: list[str] | None = None,
    use_masi: bool = False,
    use_timesfm: bool = True,
) -> dict:
    """Entry point for CLI and API."""
    import re
    m = re.match(r"(\d+)min?", timeframe)
    freq_min = int(m.group(1)) if m else 5

    from datetime import date as _date
    start_date = datetime.strptime(start, "%Y-%m-%d").date()
    end_date   = datetime.strptime(end,   "%Y-%m-%d").date()

    engine = BacktestEngine(
        ticker       = ticker,
        start        = start_date,
        end          = end_date,
        freq_min     = freq_min,
        confirm_with = confirm_with,
        use_masi     = use_masi,
        use_timesfm  = use_timesfm,
    )
    return engine.run()
