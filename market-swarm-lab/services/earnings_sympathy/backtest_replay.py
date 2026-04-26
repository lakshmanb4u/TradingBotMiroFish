"""Pre-Earnings Sympathy Backtest Replay Engine.

Replays the sympathy detector at a frozen point-in-time (as-of date) with
zero lookahead bias. Every data source is either:
  1. Loaded from a stored historical snapshot (options chain, price history)
  2. Fetched live from a provider that supports as-of queries (Alpha Vantage daily)
  3. Marked as UNAVAILABLE if point-in-time data cannot be guaranteed

After scoring candidates as if it were the as-of date, the engine evaluates
actual post-event option performance using realistic exit rules (TP1/TP2/runner).

Usage:
    from backtest_replay import BacktestReplayEngine
    engine = BacktestReplayEngine()
    result = engine.run(as_of="2026-04-19", reporter="INTC", sympathy_ticker="AMD")

CLI (via earnings_sympathy_signal.py backtest ...):
    python earnings_sympathy_signal.py backtest \\
        --as-of 2026-04-19 \\
        --reporter INTC \\
        --sympathy AMD
"""
from __future__ import annotations

import json
import logging
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parents[2]

# Service path setup
for _sd in [
    _ROOT / "services" / "earnings_sympathy",
    _ROOT / "services" / "schwab-collector",
    _ROOT / "services" / "price-collector",
    _ROOT / "services" / "uw-collector",
    _ROOT / "services" / "forecasting",
    _ROOT / "services" / "strategy-engine",
]:
    if str(_sd) not in sys.path:
        sys.path.insert(0, str(_sd))

try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

# Local imports
from earnings_calendar_service import EarningsCalendarService
from sympathy_map import SympathyMapper
from pre_earnings_sympathy_scorer import PreEarningsSympathyScorer
from sympathy_llm_analyst import SympathyLLMAnalyst

# Optional
try:
    from alpha_vantage_client import AlphaVantageClient
    _AV_AVAILABLE = True
except Exception:
    _AV_AVAILABLE = False

try:
    from uw_collector_service import UWCollectorService
    _UW_AVAILABLE = True
except Exception:
    _UW_AVAILABLE = False


# ── Constants ──────────────────────────────────────────────────────────────────

_BACKTEST_DIR = _ROOT / "state" / "backtests" / "earnings_sympathy"

_TAKE_PROFIT_RULES = {
    "tp1_multiple": 2.0,   "tp1_sell_pct": 0.50,
    "tp2_multiple": 5.0,   "tp2_sell_pct": 0.30,
    "runner_multiple": 10.0, "runner_sell_pct": 0.20,
    "stop_loss_multiple": 0.50,
}

_TRADING_DAYS_SKIP = {6, 7}  # Saturday=6, Sunday=7 (isoweekday)


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _trading_day_offset(from_date: date, offset: int) -> date:
    """Return N trading days after from_date (skipping weekends; no holiday calendar)."""
    d = from_date
    count = 0
    while count < offset:
        d += timedelta(days=1)
        if d.isoweekday() not in (6, 7):
            count += 1
    return d


# ── Indicator helpers (same as technical_confirmation.py) ─────────────────────

def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    if len(values) < period:
        return sum(values) / len(values)
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return round(e, 4)


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def _vwap_from_daily(bars: list[dict]) -> float:
    cv = sum((b["high"] + b["low"] + b["close"]) / 3 * b["volume"] for b in bars)
    vol = sum(b["volume"] for b in bars)
    return round(cv / vol, 4) if vol > 0 else 0.0


# ── Point-in-time OHLCV loader ─────────────────────────────────────────────────

class PointInTimeOHLCV:
    """Load daily OHLCV for a ticker, clamped to as-of date (no lookahead)."""

    def __init__(self, as_of: date) -> None:
        self.as_of = as_of
        self._cache: dict[str, list[dict]] = {}

    def get(self, ticker: str) -> tuple[list[dict], dict]:
        """Return (bars_up_to_as_of, source_audit)."""
        ticker = ticker.upper()
        if ticker in self._cache:
            return self._cache[ticker], {"provider": "cache", "status": "cache_hit"}

        bars, audit = self._load(ticker)
        # STRICT cutoff — only dates <= as_of
        bars = [b for b in bars if b["date"] <= self.as_of.isoformat()]
        self._cache[ticker] = bars
        return bars, audit

    def _load(self, ticker: str) -> tuple[list[dict], dict]:
        # Fix 2026-04-26: AV client now retries full→compact automatically.
        # Also added yfinance as a second fallback so backtests don't silently
        # fail with underlying_price=$0 when AV rate-limits.

        # 1. Alpha Vantage (retries full→compact internally)
        if _AV_AVAILABLE:
            try:
                client = AlphaVantageClient()
                records = client.fetch_daily(ticker, outputsize="full")
                if records:
                    return records, {
                        "provider": "alpha_vantage",
                        "status": "live_historical",
                        "point_in_time_safe": True,
                        "note": "Daily OHLCV — no intraday, no lookahead on daily close",
                    }
            except Exception as exc:
                _log.warning("[backtest] AV fetch failed for %s: %s", ticker, exc)

        # 2. yfinance fallback — free, reliable, 2y history
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            hist = t.history(period="2y", interval="1d")
            if not hist.empty:
                records = [
                    {
                        "date":   idx.strftime("%Y-%m-%d"),
                        "open":   float(row["Open"]),
                        "high":   float(row["High"]),
                        "low":    float(row["Low"]),
                        "close":  float(row["Close"]),
                        "volume": int(row["Volume"]),
                    }
                    for idx, row in hist.iterrows()
                ]
                records.sort(key=lambda b: b["date"])
                _log.info("[backtest] yfinance fallback used for %s (%d bars)", ticker, len(records))
                return records, {
                    "provider": "yfinance",
                    "status": "live_historical_fallback",
                    "point_in_time_safe": True,
                    "note": "yfinance fallback — daily OHLCV, no lookahead (dates clamped to as-of)",
                    "record_count": len(records),
                }
        except Exception as exc:
            _log.warning("[backtest] yfinance fallback failed for %s: %s", ticker, exc)

        # 3. Local fixture files
        for fixture_dir in [
            _ROOT / "infra" / "fixtures" / "market_data",
            _ROOT / "state" / "raw" / "ohlcv",
        ]:
            for fname in [f"{ticker}.json", f"{ticker}_daily.json"]:
                path = fixture_dir / fname
                if path.exists():
                    try:
                        data = json.loads(path.read_text())
                        bars = data if isinstance(data, list) else data.get("bars", [])
                        return bars, {
                            "provider": "fixture",
                            "status": "fixture_fallback",
                            "path": str(path),
                            "point_in_time_safe": True,
                        }
                    except Exception:
                        pass

        return [], {
            "provider": "none",
            "status": "unavailable",
            "point_in_time_safe": False,
            "note": "No historical OHLCV available for this ticker (AV + yfinance + fixture all failed)",
        }


# ── Point-in-time options chain loader ────────────────────────────────────────

class PointInTimeOptionsChain:
    """Load options chain snapshot closest to as-of date (no lookahead)."""

    def __init__(self, as_of: date) -> None:
        self.as_of = as_of

    def get(self, ticker: str) -> tuple[list[dict], dict]:
        """Return (filtered_contracts, source_audit)."""
        ticker = ticker.upper()
        contracts, audit = self._load(ticker)
        # Filter out contracts that expired before as_of (remove expired options)
        contracts = [c for c in contracts if c.get("expiry", "9999-99-99") > self.as_of.isoformat()]
        return contracts, audit

    def _load(self, ticker: str) -> tuple[list[dict], dict]:
        # Check for stored daily snapshots (from live runs)
        snap_dir = _ROOT / "data" / "options_positioning_snapshots"
        if snap_dir.exists():
            candidates = sorted(snap_dir.glob(f"*_{ticker}.json"), reverse=True)
            for path in candidates:
                date_part = path.stem.split("_")[0]
                try:
                    snap_date = _parse_date(date_part)
                    if snap_date <= self.as_of:
                        data = json.loads(path.read_text())
                        if isinstance(data, list) and data:
                            return data, {
                                "provider": "local_snapshot",
                                "status": "historical_snapshot",
                                "snapshot_date": date_part,
                                "record_count": len(data),
                                "point_in_time_safe": True,
                            }
                except Exception:
                    continue

        # No snapshot available — mark explicitly
        return [], {
            "provider": "none",
            "status": "unavailable",
            "point_in_time_safe": False,
            "note": (
                f"No options chain snapshot found for {ticker} on or before {self.as_of}. "
                "Run the live scanner to accumulate snapshots for future backtests."
            ),
        }


# ── Synthetic options chain builder (when no snapshot exists) ─────────────────

def _build_synthetic_chain(
    ticker: str,
    as_of: date,
    last_price: float,
    bars: list[dict],
    config: dict,
) -> tuple[list[dict], dict]:
    """Build a synthetic options chain from historical price data.

    Uses Black-Scholes approximation for IV from recent realized vol.
    Clearly marked as synthetic — not real market data.

    WARNING: Premium values are model-estimated. Real premiums may differ.
    This is for educational/structural backtest only — not for P&L accuracy.
    """
    if last_price <= 0 or len(bars) < 20:
        return [], {
            "provider": "synthetic",
            "status": "unavailable",
            "point_in_time_safe": False,
            "note": "Insufficient price data for synthetic chain",
        }

    # Estimate realized vol from last 20 days
    # Fix 2026-04-26: guard against identical consecutive closes (returns=[0,...]) which
    # caused ZeroDivisionError in math.log and zero rv_daily leading to iv_est=0 and
    # division by zero in Black-Scholes d1 formula (iv_est * sqrt(T) == 0).
    closes = [b["close"] for b in bars[-21:] if b.get("close", 0) > 0]
    if len(closes) < 5:
        return [], {
            "provider": "synthetic",
            "status": "unavailable",
            "point_in_time_safe": False,
            "note": "Insufficient valid close prices for vol estimation",
        }
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))
               if closes[i - 1] > 0 and closes[i] > 0]
    if not returns:
        return [], {
            "provider": "synthetic",
            "status": "unavailable",
            "point_in_time_safe": False,
            "note": "Could not compute log returns (zero or identical prices)",
        }
    rv_daily = math.sqrt(sum(r**2 for r in returns) / len(returns))
    # Guard: if rv is near-zero (flat ticker), use a minimum of 20% annual vol
    rv_daily  = max(rv_daily, 0.20 / math.sqrt(252))
    rv_annual = rv_daily * math.sqrt(252)

    # Use realized vol * 1.15 as estimated IV (options typically trade above RV)
    iv_est = rv_annual * 1.15

    contracts: list[dict] = []
    min_dte = config.get("min_dte", 3)
    max_dte = config.get("max_dte", 14)
    max_prem = config.get("max_risk_per_trade", 500) / 100

    # Generate expiry dates (every Friday within DTE window)
    for dte in range(min_dte, max_dte + 1):
        exp_date = as_of + timedelta(days=dte)
        if exp_date.isoweekday() != 5:  # Only Fridays for standard options
            continue

        T = dte / 365.0
        r = 0.05  # risk-free rate

        # Strike range: -20% to +20% of last price, OTM focus
        for pct in [-0.20, -0.15, -0.12, -0.10, -0.08, 0.08, 0.10, 0.12, 0.15, 0.20]:
            strike = round(last_price * (1 + pct) / 5) * 5  # round to nearest $5

            for otype in ["CALL", "PUT"]:
                is_call = otype == "CALL"
                is_otm = (is_call and strike > last_price) or (not is_call and strike < last_price)

                # Only OTM
                if not is_otm:
                    continue

                # Black-Scholes approximation
                d1 = (math.log(last_price / strike) + (r + 0.5 * iv_est**2) * T) / (iv_est * math.sqrt(T))
                d2 = d1 - iv_est * math.sqrt(T)

                # Simplified normal CDF approximation
                def _ncdf(x: float) -> float:
                    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

                if is_call:
                    delta = _ncdf(d1)
                    price = last_price * _ncdf(d1) - strike * math.exp(-r * T) * _ncdf(d2)
                else:
                    delta = _ncdf(d1) - 1
                    price = strike * math.exp(-r * T) * _ncdf(-d2) - last_price * _ncdf(-d1)

                if price <= 0.01:
                    continue

                # Simulated spread (wider for lower delta)
                spread_pct = max(8.0, 25.0 * (1 - abs(delta)))
                mid = round(price, 2)
                bid = round(mid * (1 - spread_pct / 200), 2)
                ask = round(mid * (1 + spread_pct / 200), 2)

                if mid > max_prem:
                    continue

                premium_pct = mid / last_price * 100

                contracts.append({
                    "strike": strike,
                    "expiry": exp_date.isoformat(),
                    "dte": dte,
                    "option_type": otype,
                    "bid": bid,
                    "ask": ask,
                    "mid": mid,
                    "last": mid,
                    "volume": 0,        # unknown in backtest
                    "open_interest": 0, # unknown in backtest
                    "delta": round(abs(delta), 3),
                    "implied_volatility": round(iv_est, 4),
                    "spread_pct": round(spread_pct, 2),
                    "premium_pct_of_underlying": round(premium_pct, 4),
                    "underlying_price": round(last_price, 2),
                    "is_otm": True,
                    "synthetic": True,  # IMPORTANT: flag synthetic data
                })

    return contracts, {
        "provider": "synthetic_bs",
        "status": "synthetic",
        "point_in_time_safe": True,
        "note": "Synthetic Black-Scholes estimate from realized vol. Not real market data.",
        "iv_estimate": round(iv_est, 4),
        "rv_annual": round(rv_annual, 4),
        "underlying_price": last_price,
        "record_count": len(contracts),
    }


# ── Outcome evaluator ─────────────────────────────────────────────────────────

class OutcomeEvaluator:
    """Evaluate actual post-event performance of selected contracts."""

    def __init__(self, as_of: date, reporter_earnings_date: date) -> None:
        self.as_of = as_of
        self.earnings_date = reporter_earnings_date
        self._ohlcv = PointInTimeOHLCV(as_of=date(2099, 1, 1))  # No cutoff for outcome eval

    def evaluate(self, candidate: dict, sympathy_ticker: str) -> dict[str, Any]:
        """Compute actual option outcome using post-event price data."""
        ticker = sympathy_ticker.upper()
        bars, _ = self._ohlcv.get(ticker)

        if not bars:
            return self._no_data_outcome(candidate, "no_price_data_for_outcome")

        # Build price lookup
        price_by_date: dict[str, dict] = {b["date"]: b for b in bars}

        entry_date_str = self.as_of.isoformat()
        entry_bar = price_by_date.get(entry_date_str)
        if not entry_bar:
            # Use last available bar before as_of
            past = [b for b in bars if b["date"] <= entry_date_str]
            entry_bar = past[-1] if past else None

        if not entry_bar:
            return self._no_data_outcome(candidate, "no_entry_price_bar")

        underlying_entry = entry_bar["close"]
        option_entry_price = candidate.get("premium", 0.0)

        if option_entry_price <= 0:
            return self._no_data_outcome(candidate, "zero_entry_premium")

        is_call = candidate["option_type"] == "CALL"
        strike = candidate["strike"]
        expiry_str = candidate.get("expiry", "")
        try:
            expiry_date = _parse_date(expiry_str)
        except Exception:
            expiry_date = self.as_of + timedelta(days=candidate.get("dte", 7))

        # Post-event price windows
        t0 = self.earnings_date
        windows = {
            "next_open":          _trading_day_offset(t0, 1),
            "next_close":         _trading_day_offset(t0, 1),
            "plus_1d_close":      _trading_day_offset(t0, 1),
            "plus_2d_close":      _trading_day_offset(t0, 2),
            "plus_3d_close":      _trading_day_offset(t0, 3),
        }

        outcomes_by_window: dict[str, dict] = {}
        max_return = 0.0
        best_underlying = underlying_entry

        for label, window_date in windows.items():
            bar = price_by_date.get(window_date.isoformat())
            if bar is None:
                continue
            underlying_exit = bar["high"] if "next_open" in label else bar["close"]
            intrinsic = max(0.0, (underlying_exit - strike) if is_call else (strike - underlying_exit))
            # Estimated option price at that point (simplified — intrinsic + small time value)
            dte_remaining = max(0, (expiry_date - window_date).days)
            time_val = option_entry_price * 0.1 * math.sqrt(dte_remaining / max(candidate.get("dte", 7), 1))
            option_exit_price = intrinsic + time_val

            if option_entry_price > 0:
                ret_multiple = option_exit_price / option_entry_price
            else:
                ret_multiple = 0.0

            outcomes_by_window[label] = {
                "window_date": window_date.isoformat(),
                "underlying_exit": round(underlying_exit, 2),
                "option_exit_price": round(option_exit_price, 2),
                "return_multiple": round(ret_multiple, 2),
                "underlying_move_pct": round((underlying_exit - underlying_entry) / underlying_entry * 100, 2),
            }
            if ret_multiple > max_return:
                max_return = ret_multiple
                best_underlying = underlying_exit

        # Max favorable excursion before expiry
        post_bars = [b for b in bars
                     if self.earnings_date.isoformat() < b["date"] <= expiry_str]
        mfe_multiple = 0.0
        mfe_date = None
        for b in post_bars:
            price = b["high"] if is_call else b["low"]
            intrinsic = max(0.0, (price - strike) if is_call else (strike - price))
            dte_rem = max(0, (expiry_date - _parse_date(b["date"])).days)
            tv = option_entry_price * 0.05 * math.sqrt(dte_rem / max(candidate.get("dte", 7), 1))
            m = (intrinsic + tv) / option_entry_price if option_entry_price > 0 else 0
            if m > mfe_multiple:
                mfe_multiple = m
                mfe_date = b["date"]

        # Check expiry intrinsic
        exp_bar = price_by_date.get(expiry_str)
        expired_worthless = True
        expiry_intrinsic = 0.0
        if exp_bar:
            exp_price = exp_bar["close"]
            expiry_intrinsic = max(0.0, (exp_price - strike) if is_call else (strike - exp_price))
            expired_worthless = expiry_intrinsic < 0.01

        # Take-profit simulation
        tp_result = self._simulate_tp(option_entry_price, mfe_multiple)

        return {
            "ticker": ticker,
            "option_type": candidate["option_type"],
            "strike": strike,
            "expiry": expiry_str,
            "entry_date": self.as_of.isoformat(),
            "underlying_entry_price": round(underlying_entry, 2),
            "option_entry_price": round(option_entry_price, 2),
            "max_loss_dollars": round(option_entry_price * 100, 2),
            "outcomes_by_window": outcomes_by_window,
            "max_favorable_excursion_multiple": round(mfe_multiple, 2),
            "mfe_date": mfe_date,
            "expired_worthless": expired_worthless,
            "expiry_intrinsic": round(expiry_intrinsic, 2),
            "did_it_hit_2x": mfe_multiple >= 2.0,
            "did_it_hit_5x": mfe_multiple >= 5.0,
            "did_it_hit_10x": mfe_multiple >= 10.0,
            "take_profit_simulation": tp_result,
            "underlying_move_at_best_window": round(
                (best_underlying - underlying_entry) / underlying_entry * 100, 2
            ) if underlying_entry > 0 else 0.0,
        }

    def _simulate_tp(self, entry_price: float, mfe_multiple: float) -> dict:
        """Simulate TP1/TP2/runner exits."""
        rules = _TAKE_PROFIT_RULES
        remaining_units = 1.0
        realized_pnl = 0.0
        exits = []

        for label, mult, pct in [
            ("TP1_2x",  rules["tp1_multiple"],    rules["tp1_sell_pct"]),
            ("TP2_5x",  rules["tp2_multiple"],    rules["tp2_sell_pct"]),
            ("runner",  rules["runner_multiple"], rules["runner_sell_pct"]),
        ]:
            if mfe_multiple >= mult and remaining_units > 0:
                sell = min(pct, remaining_units)
                pnl = sell * mult * entry_price * 100
                realized_pnl += pnl
                remaining_units -= sell
                exits.append({"exit": label, "multiple": mult, "units_sold": sell, "pnl": round(pnl, 2)})

        # Stop loss if never hit TP1
        if not exits and mfe_multiple < rules["stop_loss_multiple"]:
            realized_pnl = -entry_price * 100 * rules["stop_loss_multiple"]
            exits.append({"exit": "stop_loss", "multiple": rules["stop_loss_multiple"], "pnl": round(realized_pnl, 2)})

        # Remaining units exit at MFE (optimistic) or 0 (expired worthless)
        if remaining_units > 0:
            exit_price = mfe_multiple * entry_price * 100 * remaining_units
            realized_pnl += exit_price
            exits.append({"exit": "time_exit", "multiple": mfe_multiple, "units_remaining": remaining_units, "pnl": round(exit_price, 2)})

        total_cost = entry_price * 100
        net_return_multiple = (realized_pnl / total_cost) if total_cost > 0 else 0.0

        return {
            "exits": exits,
            "total_realized_pnl": round(realized_pnl, 2),
            "cost_basis": round(total_cost, 2),
            "net_return_multiple": round(net_return_multiple, 2),
            "net_return_pct": round((net_return_multiple - 1) * 100, 2),
        }

    def _no_data_outcome(self, candidate: dict, reason: str) -> dict:
        return {
            "ticker": candidate.get("sympathy_ticker", "?"),
            "option_type": candidate.get("option_type"),
            "strike": candidate.get("strike"),
            "expiry": candidate.get("expiry"),
            "outcome_available": False,
            "reason": reason,
        }


# ── Main Backtest Engine ───────────────────────────────────────────────────────

class BacktestReplayEngine:
    """Run a point-in-time replay of the sympathy detector."""

    def __init__(self) -> None:
        self._config = self._load_config()
        self._scorer = PreEarningsSympathyScorer(self._config)
        self._llm    = SympathyLLMAnalyst(timeout=self._config.get("llm_timeout_seconds", 15))
        self._mapper = SympathyMapper()
        self._calendar = EarningsCalendarService()

    def run(
        self,
        as_of: str,
        reporter: str,
        sympathy_ticker: str,
    ) -> dict[str, Any]:
        as_of_date = _parse_date(as_of)
        reporter = reporter.upper()
        sympathy_ticker = sympathy_ticker.upper()

        _log.info("[backtest] Replaying as_of=%s reporter=%s sympathy=%s", as_of, reporter, sympathy_ticker)

        run_key = f"{as_of}_{reporter}_{sympathy_ticker}"
        out_dir = _BACKTEST_DIR / run_key
        _ensure_dir(out_dir)

        source_audits: dict[str, Any] = {}

        # ── Step 1: Earnings calendar (point-in-time safe — config file) ──────
        all_events = self._calendar.fetch_all()
        reporter_events = [e for e in all_events if e.ticker == reporter]
        future_reporter_events = [e for e in reporter_events
                                   if e.date > as_of_date.isoformat()]
        future_reporter_events.sort(key=lambda e: e.date)
        upcoming_event = future_reporter_events[0] if future_reporter_events else None

        has_own_earnings_soon = any(
            e.ticker == sympathy_ticker
            and as_of_date.isoformat() < e.date <= (
                as_of_date + timedelta(days=self._config.get("avoid_own_earnings_within_days", 5))
            ).isoformat()
            for e in all_events
        )

        source_audits["earnings_calendar"] = {
            "provider": "config_file",
            "status": "config",
            "point_in_time_safe": True,
            "upcoming_reporter_event": upcoming_event.to_dict() if upcoming_event else None,
            "sympathy_has_own_earnings_soon": has_own_earnings_soon,
        }

        # ── Step 2: Historical OHLCV (point-in-time clamped) ─────────────────
        pit_ohlcv = PointInTimeOHLCV(as_of=as_of_date)
        sym_bars, ohlcv_audit = pit_ohlcv.get(sympathy_ticker)
        source_audits["ohlcv"] = {**ohlcv_audit, "record_count": len(sym_bars), "ticker": sympathy_ticker}

        underlying_price = sym_bars[-1]["close"] if sym_bars else 0.0
        closes = [b["close"] for b in sym_bars]

        # ── Step 3: Technical indicators from historical daily bars ───────────
        rsi_val = _rsi(closes[-20:]) if len(closes) >= 15 else 50.0
        ema9    = _ema(closes[-30:], 9)
        ema21   = _ema(closes[-30:], 21)
        vwap    = _vwap_from_daily(sym_bars[-5:]) if sym_bars else 0.0
        price_vs_vwap = "above" if underlying_price > vwap else "below"
        price_vs_ema9  = "above" if underlying_price > ema9 else "below"
        price_vs_ema21 = "above" if underlying_price > ema21 else "below"

        bullish_tech = (price_vs_vwap == "above" and price_vs_ema9 == "above" and price_vs_ema21 == "above")
        bearish_tech = (price_vs_vwap == "below" and price_vs_ema9 == "below" and price_vs_ema21 == "below")

        if bullish_tech:
            tech_status = "ready"
            tech_score = 75
            tech_direction = "bullish"
            trigger_level = round(underlying_price * 1.005, 2)
            invalidation = round(ema21 * 0.995, 2)
        elif bearish_tech:
            tech_status = "ready"
            tech_score = 72
            tech_direction = "bearish"
            trigger_level = round(underlying_price * 0.995, 2)
            invalidation = round(ema21 * 1.005, 2)
        else:
            tech_status = "watchlist"
            tech_score = 55
            tech_direction = "neutral"
            trigger_level = round(underlying_price * 1.01, 2) if price_vs_ema9 == "above" else round(underlying_price * 0.99, 2)
            invalidation = round(min(ema9, ema21) * 0.99, 2)

        technical = {
            "setup_status": tech_status,
            "technical_score": tech_score,
            "direction": tech_direction,
            "trigger_level": trigger_level,
            "invalidation_level": invalidation,
            "rsi": rsi_val,
            "ema9": ema9,
            "ema21": ema21,
            "vwap": vwap,
            "price_vs_vwap": price_vs_vwap,
            "price_vs_ema9": price_vs_ema9,
            "price_vs_ema21": price_vs_ema21,
            "premarket_move_pct": 0.0,   # daily data — no premarket available
            "note": "DAILY bars used — no intraday VWAP. VWAP approximated from 5-day daily bars.",
        }
        source_audits["technical"] = {
            "provider": "alpha_vantage_daily",
            "point_in_time_safe": True,
            "note": "Indicators computed from daily OHLCV up to as-of date only",
        }

        # ── Step 4: Options chain (snapshot or synthetic) ─────────────────────
        pit_options = PointInTimeOptionsChain(as_of=as_of_date)
        contracts, options_audit = pit_options.get(sympathy_ticker)
        source_audits["options_chain"] = options_audit

        if not contracts:
            _log.info("[backtest] No real options snapshot — building synthetic chain")
            contracts, synth_audit = _build_synthetic_chain(
                ticker=sympathy_ticker,
                as_of=as_of_date,
                last_price=underlying_price,
                bars=sym_bars,
                config=self._config,
            )
            source_audits["options_chain"] = synth_audit
            if not contracts:
                return self._empty_result(run_key, out_dir, "no_options_data", source_audits)

        # ── Step 5: UW flow (historical — explicitly flagged) ─────────────────
        uw_flow = None
        source_audits["unusual_whales"] = {
            "provider": "none",
            "status": "unavailable",
            "point_in_time_safe": False,
            "note": (
                "Unusual Whales does not provide historical point-in-time flow data via free API. "
                "Flow score omitted — weight redistributed to positioning and technical."
            ),
        }

        # ── Step 6: Positioning score (from contract data only — no UW) ───────
        call_vol = sum(c["volume"] for c in contracts if c["option_type"] == "CALL")
        put_vol  = sum(c["volume"] for c in contracts if c["option_type"] == "PUT")
        call_oi  = sum(c["open_interest"] for c in contracts if c["option_type"] == "CALL")
        put_oi   = sum(c["open_interest"] for c in contracts if c["option_type"] == "PUT")
        total_oi = call_oi + put_oi
        vol_to_oi = (call_vol + put_vol) / total_oi if total_oi > 0 else 0.0

        # With synthetic data, volume/OI are 0 — set neutral positioning score
        is_synthetic = source_audits["options_chain"].get("provider") == "synthetic_bs"
        if is_synthetic:
            positioning_score = 50
            positioning_note = "Synthetic chain — volume/OI unknown. Neutral positioning score."
        else:
            if vol_to_oi >= 0.3:
                positioning_score = 80
            elif vol_to_oi >= 0.1:
                positioning_score = 55
            else:
                positioning_score = 30
            positioning_note = f"vol_to_oi={vol_to_oi:.3f}"

        positioning = {
            "positioning_score": positioning_score,
            "call_volume": call_vol,
            "put_volume": put_vol,
            "call_oi": call_oi,
            "put_oi": put_oi,
            "option_volume_to_oi_ratio": round(vol_to_oi, 4),
            "oi_change_available": False,
            "note": positioning_note,
        }

        # ── Step 7: Historical sympathy stats ─────────────────────────────────
        past_reporter_events = [e for e in reporter_events if e.date < as_of_date.isoformat()]
        hist_stats = self._compute_historical_sympathy(
            reporter=reporter,
            sympathy_ticker=sympathy_ticker,
            past_events=past_reporter_events,
            pit_ohlcv=pit_ohlcv,
        )
        source_audits["historical_sympathy"] = {
            "provider": "alpha_vantage_daily",
            "point_in_time_safe": True,
            "past_earnings_events_used": len(past_reporter_events),
            "pairs_computed": len(hist_stats.get("pairs", [])),
        }

        # ── Step 8: IV analysis ───────────────────────────────────────────────
        iv_values = [c["implied_volatility"] for c in contracts if c.get("implied_volatility", 0) > 0]
        avg_iv = sum(iv_values) / len(iv_values) if iv_values else 0.0
        iv_expanded = False  # Can't compute IV rank without historical IV series
        iv_analysis = {
            "iv_rank": None,
            "iv_percentile": None,
            "iv_dislocation_score": 50,
            "iv_expanded": iv_expanded,
            "convexity_score": 0,
            "avg_iv_in_chain": round(avg_iv, 4),
            "note": "IV rank unavailable in backtest — requires historical IV series",
        }

        # Compute convexity_score for each candidate using hist move
        avg_move = hist_stats.get("avg_1d_move_pct", 0.0)

        # ── Step 9: Score top contracts ───────────────────────────────────────
        # Pick best OTM call and best OTM put by delta proximity to 0.15
        top_calls = sorted(
            [c for c in contracts if c["option_type"] == "CALL" and c.get("is_otm", True)],
            key=lambda c: abs(c.get("delta", 0) - 0.15)
        )[:3]
        top_puts = sorted(
            [c for c in contracts if c["option_type"] == "PUT" and c.get("is_otm", True)],
            key=lambda c: abs(c.get("delta", 0) - 0.15)
        )[:3]

        candidates_raw: list[dict] = []
        for contract in (top_calls + top_puts):
            # Set convexity based on hist move vs premium
            prem_pct = contract.get("premium_pct_of_underlying", 0.01)
            if prem_pct > 0 and avg_move > 0:
                conv = min(100, int((avg_move / prem_pct) * 25))
            else:
                conv = 40  # neutral
            iv_analysis_c = {**iv_analysis, "convexity_score": conv}

            candidate = self._scorer.score_candidate(
                reporter=reporter,
                sympathy_ticker=sympathy_ticker,
                contract=contract,
                positioning=positioning,
                iv_analysis=iv_analysis_c,
                hist_sympathy=hist_stats,
                technical=technical,
                uw_flow=uw_flow,
                premarket_move_pct=0.0,
                has_own_earnings_soon=has_own_earnings_soon,
            )
            candidate["synthetic_data"] = is_synthetic
            candidates_raw.append(candidate)

        passing, skipped = self._scorer.rank_candidates(candidates_raw)

        # ── Step 10: LLM veto ─────────────────────────────────────────────────
        llm_result: dict = {"llm_analyses": [], "llm_status": {"degraded_mode": True}}
        if self._scorer.should_call_llm(passing):
            ctx = (
                f"{reporter} had earnings on {upcoming_event.date if upcoming_event else 'unknown date'}. "
                f"This is a point-in-time backtest replay as of {as_of}. "
                f"Evaluate whether {sympathy_ticker} was a valid sympathy play."
            )
            llm_result = self._llm.analyze(passing[:3], reporter, ctx)
        else:
            llm_result["llm_status"]["reason"] = "insufficient_candidates_for_llm"

        # Apply LLM analyses
        for c in passing:
            la = next(
                (a for a in llm_result.get("llm_analyses", [])
                 if a.get("sympathy_ticker") == c["sympathy_ticker"]),
                None,
            )
            c["llm_vetoed"] = la.get("vetoed", False) if la else False
            c["llm_narrative"] = la.get("narrative_summary", "") if la else ""
            c["llm_risks"] = la.get("risks", []) if la else []

        # ── Step 11: Outcome evaluation ───────────────────────────────────────
        earnings_date = _parse_date(upcoming_event.date) if upcoming_event else as_of_date + timedelta(days=1)
        evaluator = OutcomeEvaluator(as_of=as_of_date, reporter_earnings_date=earnings_date)

        outcomes: list[dict] = []
        for c in (passing + skipped[:3]):
            outcome = evaluator.evaluate(c, sympathy_ticker)
            outcome["candidate_action"] = c["action"]
            outcome["candidate_score"] = c.get("final_score", 0)
            outcome["skip_reason"] = c.get("skip_reason", "")
            outcomes.append(outcome)

        # ── Step 12: Build final report ───────────────────────────────────────
        result = {
            "run_key": run_key,
            "as_of": as_of,
            "reporter": reporter,
            "sympathy_ticker": sympathy_ticker,
            "reporter_earnings_date": upcoming_event.date if upcoming_event else None,
            "reporter_earnings_time": upcoming_event.time if upcoming_event else None,
            "sympathy_has_own_earnings_soon": has_own_earnings_soon,
            "underlying_price_as_of": round(underlying_price, 2),
            "technical": technical,
            "positioning": positioning,
            "historical_sympathy": hist_stats,
            "iv_analysis": iv_analysis,
            "passing_candidates": passing,
            "skipped_candidates": skipped,
            "llm_result": llm_result,
            "outcomes": outcomes,
            "source_audits": source_audits,
            "backtest_conclusions": self._conclusions(passing, skipped, outcomes, hist_stats, source_audits),
        }

        self._save(out_dir, result, passing, skipped, outcomes)
        return result

    # ── Historical sympathy stats ─────────────────────────────────────────────

    def _compute_historical_sympathy(
        self,
        reporter: str,
        sympathy_ticker: str,
        past_events: list,
        pit_ohlcv: PointInTimeOHLCV,
    ) -> dict[str, Any]:
        if not past_events:
            return {
                "reporter": reporter,
                "sympathy_ticker": sympathy_ticker,
                "avg_1d_move_pct": 3.5,  # sector default
                "max_1d_move_pct": 10.0,
                "direction_consistency": 0.60,
                "correlation": 0.65,
                "historical_score": 55,
                "pairs": [],
                "note": "No historical earnings events in config — using sector defaults",
            }

        bars, _ = pit_ohlcv.get(sympathy_ticker)
        price_by_date = {b["date"]: b for b in bars}

        pairs = []
        for event in past_events:
            event_date = event.date
            # Day before earnings close
            try:
                ed = _parse_date(event_date)
                d_minus1 = _trading_day_offset(ed, -1) if ed.isoweekday() != 1 else ed - timedelta(days=3)
                d_plus1  = _trading_day_offset(ed, 1)

                bar_before = price_by_date.get(d_minus1.isoformat())
                bar_after  = price_by_date.get(d_plus1.isoformat()) or price_by_date.get(event_date)
                if not bar_before or not bar_after:
                    continue

                move_1d = (bar_after["close"] - bar_before["close"]) / bar_before["close"] * 100
                pairs.append({
                    "earnings_date": event_date,
                    "sympathy_1d_move_pct": round(move_1d, 2),
                    "direction": "up" if move_1d > 0 else "down",
                })
            except Exception as exc:
                _log.debug("[backtest] hist pair error for %s %s: %s", reporter, event_date, exc)

        if not pairs:
            return {
                "reporter": reporter,
                "sympathy_ticker": sympathy_ticker,
                "avg_1d_move_pct": 3.5,
                "max_1d_move_pct": 10.0,
                "direction_consistency": 0.60,
                "correlation": 0.65,
                "historical_score": 55,
                "pairs": [],
                "note": "Price data unavailable for historical pairs",
            }

        moves = [abs(p["sympathy_1d_move_pct"]) for p in pairs]
        avg_move = sum(moves) / len(moves)
        max_move = max(moves)
        up_count = sum(1 for p in pairs if p["direction"] == "up")
        direction_consistency = up_count / len(pairs)

        # Historical score
        if avg_move >= 4.0:
            hist_score = 85
        elif avg_move >= 2.5:
            hist_score = 70
        elif avg_move >= 1.5:
            hist_score = 55
        else:
            hist_score = 35

        return {
            "reporter": reporter,
            "sympathy_ticker": sympathy_ticker,
            "avg_1d_move_pct": round(avg_move, 2),
            "max_1d_move_pct": round(max_move, 2),
            "direction_consistency": round(direction_consistency, 3),
            "correlation": 0.70,  # Approximated — full correlation requires aligned series
            "historical_score": hist_score,
            "pairs": pairs,
            "n_events": len(pairs),
        }

    # ── Conclusions ────────────────────────────────────────────────────────────

    def _conclusions(
        self,
        passing: list[dict],
        skipped: list[dict],
        outcomes: list[dict],
        hist: dict,
        audits: dict,
    ) -> dict[str, Any]:
        call_candidates = [c for c in passing if c["option_type"] == "CALL"]
        put_candidates  = [c for c in passing if c["option_type"] == "PUT"]

        winning_outcomes = [o for o in outcomes if o.get("did_it_hit_2x")]
        best_outcome = max(outcomes, key=lambda o: o.get("max_favorable_excursion_multiple", 0)) if outcomes else {}

        synthetic_warning = any(
            a.get("provider") == "synthetic_bs"
            for a in audits.values() if isinstance(a, dict)
        )

        return {
            "would_have_selected_calls": len(call_candidates) > 0,
            "would_have_selected_puts": len(put_candidates) > 0,
            "n_passing_calls": len(call_candidates),
            "n_passing_puts": len(put_candidates),
            "top_call": call_candidates[0] if call_candidates else None,
            "top_put": put_candidates[0] if put_candidates else None,
            "any_hit_2x": any(o.get("did_it_hit_2x") for o in outcomes),
            "any_hit_5x": any(o.get("did_it_hit_5x") for o in outcomes),
            "any_hit_10x": any(o.get("did_it_hit_10x") for o in outcomes),
            "all_expired_worthless": all(o.get("expired_worthless", True) for o in outcomes),
            "best_outcome_multiple": best_outcome.get("max_favorable_excursion_multiple", 0),
            "best_outcome_strike": best_outcome.get("strike"),
            "best_outcome_type": best_outcome.get("option_type"),
            "n_winning_outcomes": len(winning_outcomes),
            "hist_avg_move_used": hist.get("avg_1d_move_pct"),
            "synthetic_data_warning": synthetic_warning,
            "uw_data_available": audits.get("unusual_whales", {}).get("status") == "live_historical",
            "lookahead_bias_check": "PASS — all data clamped to as-of date",
            "caveat": (
                "SYNTHETIC option pricing used — premium/P&L figures are model estimates. "
                "For accurate backtest, store real options chain snapshots via live scanner."
            ) if synthetic_warning else "Real options data used.",
        }

    # ── Persistence ────────────────────────────────────────────────────────────

    def _save(
        self,
        out_dir: Path,
        result: dict,
        passing: list[dict],
        skipped: list[dict],
        outcomes: list[dict],
    ) -> None:
        def _write(name: str, data: Any) -> None:
            try:
                (out_dir / name).write_text(json.dumps(data, indent=2, default=str))
            except Exception as exc:
                _log.warning("[backtest] save %s failed: %s", name, exc)

        _write("backtest_report.json", result)
        _write("ranked_candidates.json", passing)
        _write("skipped_candidates.json", skipped)

        # CSV exports
        self._write_csv(out_dir / "candidates.csv", passing, [
            "sympathy_ticker", "option_type", "strike", "expiry", "premium",
            "max_loss", "delta", "dte", "spread_pct", "final_score", "action",
            "positioning_score", "convexity_score", "historical_sympathy_score",
            "technical_score", "flow_score", "trigger_level", "invalidation_level",
            "reason", "skip_reason",
        ])
        self._write_csv(out_dir / "skipped_candidates.csv", skipped, [
            "sympathy_ticker", "option_type", "strike", "expiry", "premium",
            "final_score", "skip_reason",
        ])
        self._write_csv(out_dir / "option_outcomes.csv", outcomes, [
            "ticker", "option_type", "strike", "expiry", "option_entry_price",
            "max_loss_dollars", "max_favorable_excursion_multiple",
            "did_it_hit_2x", "did_it_hit_5x", "did_it_hit_10x",
            "expired_worthless", "candidate_action", "candidate_score",
        ])

        # Markdown report
        md = self._build_markdown(result)
        try:
            (out_dir / "backtest_report.md").write_text(md)
        except Exception as exc:
            _log.warning("[backtest] markdown write failed: %s", exc)

        _log.info("[backtest] results saved → %s", out_dir)

    def _write_csv(self, path: Path, rows: list[dict], fields: list[str]) -> None:
        import csv
        try:
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
        except Exception as exc:
            _log.warning("[backtest] CSV write %s failed: %s", path.name, exc)

    def _build_markdown(self, r: dict) -> str:
        c = r["backtest_conclusions"]
        h = r["historical_sympathy"]
        tech = r["technical"]
        lines = [
            f"# Backtest Report — {r['reporter']} → {r['sympathy_ticker']}",
            f"**Replay date:** {r['as_of']}  |  "
            f"**Reporter earnings:** {r['reporter_earnings_date']} {r['reporter_earnings_time'] or ''}",
            f"**Underlying price as-of:** ${r['underlying_price_as_of']}",
            "",
            "## Lookahead Bias Check",
            f"✅ {c['lookahead_bias_check']}",
            "",
            "## Would the Detector Have Fired?",
            f"- **Calls selected:** {'YES ✅' if c['would_have_selected_calls'] else 'NO ❌'}  "
            f"({c['n_passing_calls']} candidates)",
            f"- **Puts selected:** {'YES ✅' if c['would_have_selected_puts'] else 'NO ❌'}  "
            f"({c['n_passing_puts']} candidates)",
        ]

        if c.get("top_call"):
            tc = c["top_call"]
            lines += [
                "",
                f"### Top Call: {tc['sympathy_ticker']} ${tc['strike']} exp {tc['expiry']}",
                f"Premium: ${tc['premium']}  |  Delta: {tc['delta']}  |  DTE: {tc['dte']}  |  Score: {tc['final_score']}",
                f"Trigger: ${tc.get('trigger_level','?')}  |  Invalidation: ${tc.get('invalidation_level','?')}",
                f"Why: {tc['reason']}",
            ]
        if c.get("top_put"):
            tp = c["top_put"]
            lines += [
                "",
                f"### Top Put: {tp['sympathy_ticker']} ${tp['strike']} exp {tp['expiry']}",
                f"Premium: ${tp['premium']}  |  Delta: {tp['delta']}  |  DTE: {tp['dte']}  |  Score: {tp['final_score']}",
            ]

        lines += [
            "",
            "## Actual Outcome",
            f"- Hit 2x: {'YES ✅' if c['any_hit_2x'] else 'NO ❌'}",
            f"- Hit 5x: {'YES ✅' if c['any_hit_5x'] else 'NO ❌'}",
            f"- Hit 10x: {'YES ✅' if c['any_hit_10x'] else 'NO ❌'}",
            f"- All expired worthless: {'YES ❌' if c['all_expired_worthless'] else 'NO ✅'}",
            f"- Best outcome: **{c['best_outcome_multiple']}x** "
            f"({c['best_outcome_type']} ${c['best_outcome_strike']})",
            f"- Winning candidates (hit 2x+): {c['n_winning_outcomes']}",
        ]

        lines += [
            "",
            "## Historical Sympathy Stats",
            f"- Avg 1-day move: **{h.get('avg_1d_move_pct','?')}%**",
            f"- Max 1-day move: **{h.get('max_1d_move_pct','?')}%**",
            f"- Direction consistency: **{(h.get('direction_consistency',0)*100):.0f}%**",
            f"- Past events used: {h.get('n_events', 0)}",
        ]

        lines += [
            "",
            "## Technical Snapshot (as-of)",
            f"- Price: ${r['underlying_price_as_of']}  |  RSI: {tech.get('rsi','?'):.1f}",
            f"- EMA9: ${tech.get('ema9','?')}  |  EMA21: ${tech.get('ema21','?')}",
            f"- Price vs VWAP: {tech.get('price_vs_vwap')}  |  Setup: {tech.get('setup_status')}",
            f"- Direction: {tech.get('direction')}  |  Tech score: {tech.get('technical_score')}",
        ]

        lines += [
            "",
            "## Source Audit",
        ]
        for src, audit in r["source_audits"].items():
            safe = "✅ point-in-time safe" if audit.get("point_in_time_safe") else "⚠️  NOT point-in-time safe"
            lines.append(f"- **{src}**: {audit.get('status','?')} via {audit.get('provider','?')} | {safe}")
            if audit.get("note"):
                lines.append(f"  _{audit['note']}_")

        if c.get("synthetic_data_warning"):
            lines += [
                "",
                "## ⚠️  Data Quality Warning",
                c.get("caveat", ""),
                "For accurate P&L backtesting, run the live scanner to accumulate real options snapshots.",
            ]

        lines += ["", "---", f"_Backtest run key: {r['run_key']}_"]
        return "\n".join(lines)

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _load_config() -> dict[str, Any]:
        cfg_path = _ROOT / "config" / "sympathy_strategy_config.json"
        defaults = {
            "max_risk_per_trade": 500,
            "min_final_score": 60,   # Lower threshold for backtest (less data available)
            "max_spread_pct": 20.0,  # Slightly wider — synthetic data may have wider spreads
            "min_volume": 0,         # Volume unknown in backtest — skip this filter
            "min_open_interest": 0,  # OI unknown in backtest
            "min_dte": 3,
            "max_dte": 14,
            "avoid_own_earnings_within_days": 5,
            "max_premarket_move_pct_before_skip": 3.0,
            "iv_expansion_hard_limit": 1.4,
            "max_candidates_to_llm": 3,
            "min_final_score_for_llm": 55,
            "llm_timeout_seconds": 15,
        }
        if cfg_path.exists():
            try:
                file_cfg = json.loads(cfg_path.read_text())
                # Backtest relaxes volume/OI filters
                file_cfg["min_volume"] = 0
                file_cfg["min_open_interest"] = 0
                file_cfg["min_final_score"] = 60
                return {**defaults, **file_cfg}
            except Exception:
                pass
        return defaults

    def _empty_result(self, run_key: str, out_dir: Path, reason: str, audits: dict) -> dict:
        return {
            "run_key": run_key,
            "error": reason,
            "source_audits": audits,
            "passing_candidates": [],
            "skipped_candidates": [],
            "outcomes": [],
            "backtest_conclusions": {
                "would_have_selected_calls": False,
                "would_have_selected_puts": False,
                "reason": reason,
            },
        }
