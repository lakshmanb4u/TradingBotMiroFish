"""Backtester: replays strategy+risk engine on stored OHLCV artifacts."""
from __future__ import annotations

import csv
import json
import math
import sys
from datetime import date
from pathlib import Path
from statistics import mean, stdev
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]

for _sd in [
    _ROOT / "services" / "strategy-engine",
    _ROOT / "services" / "risk-engine",
    _ROOT / "services" / "forecasting",
]:
    _sp = str(_sd)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from strategy_engine_service import StrategyEngineService
from risk_engine_service import RiskEngineService


class BacktesterService:
    _FIXTURE_DIR = _ROOT / "infra" / "fixtures" / "market_data"
    _PARQUET_DIR = _ROOT / "data" / "market_data" / "ohlcv"
    _REPORT_DIR = _ROOT / "state" / "reports"

    def run(
        self, ticker: str, horizon: str = "1d", fixture_mode: bool = False
    ) -> dict:
        ticker = ticker.upper()
        series = self._load_series(ticker, fixture_mode)
        if len(series) < 30:
            return {
                "ticker": ticker,
                "horizon": horizon,
                "error": f"Insufficient data: need 30 bars, got {len(series)}",
                "total_bars": len(series),
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "avg_return": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
                "by_strategy": {},
                "equity_curve": [],
                "trades": [],
                "report_paths": {},
            }

        strategy_svc = StrategyEngineService()
        risk_svc = RiskEngineService()

        window_size = 60
        future_map = {"1h": 1, "1d": 1, "3d": 3, "5d": 5}
        future_window = future_map.get(horizon, 1)

        trades: list[dict] = []
        bar_records: list[dict] = []

        for i in range(window_size, len(series)):
            window = series[i - window_size : i]
            close_prices = [r["close"] for r in window]

            context = self._build_context(ticker, close_prices)
            signal = strategy_svc.generate_signal(ticker, context, horizon)
            risk = risk_svc.evaluate(signal, context)

            bar_records.append(
                {
                    "date": series[i].get("date", str(i)),
                    "signal_trade": signal["trade"],
                    "approved": risk["approved"],
                    "confidence": signal["confidence"],
                    "strategy_type": signal["strategy_type"],
                }
            )

            if not risk["approved"] or signal["trade"] == "HOLD":
                continue

            # Need enough future bars for outcome
            if i + future_window >= len(series):
                continue

            entry_close = series[i]["close"]
            exit_close = series[i + future_window]["close"]
            actual_move = (exit_close - entry_close) / max(abs(entry_close), 1e-9)

            option_return = self._compute_option_return(
                signal["trade"],
                actual_move,
                risk["stop_loss_pct"],
                risk["take_profit_pct"],
            )

            trades.append(
                {
                    "date": series[i].get("date", str(i)),
                    "trade": signal["trade"],
                    "strategy_type": signal["strategy_type"],
                    "confidence": signal["confidence"],
                    "entry_close": entry_close,
                    "exit_close": exit_close,
                    "actual_move": round(actual_move, 6),
                    "option_return": round(option_return, 6),
                    "bars_held": future_window,
                }
            )

        metrics = self._compute_metrics(trades)
        equity_curve = self._build_equity_curve(trades, series, window_size)
        report_paths = self._persist_results(ticker, horizon, metrics, trades, equity_curve)

        return {
            "ticker": ticker,
            "horizon": horizon,
            "total_bars": len(series),
            "total_trades": metrics["total_trades"],
            "winning_trades": metrics["winning_trades"],
            "losing_trades": metrics["losing_trades"],
            "win_rate": metrics["win_rate"],
            "avg_return": metrics["avg_return"],
            "max_drawdown": metrics["max_drawdown"],
            "sharpe_ratio": metrics["sharpe_ratio"],
            "by_strategy": metrics["by_strategy"],
            "equity_curve": equity_curve,
            "trades": trades,
            "report_paths": report_paths,
        }

    # ------------------------------------------------------------------ data loading

    def _load_series(self, ticker: str, fixture_mode: bool) -> list[dict]:
        # Try parquet first (unless fixture_mode)
        if not fixture_mode:
            parquet_path = self._PARQUET_DIR / f"{ticker}.parquet"
            if parquet_path.exists():
                try:
                    import pandas as pd
                    df = pd.read_parquet(parquet_path)
                    if "close" in df.columns:
                        records = []
                        for idx, row in df.iterrows():
                            rec: dict[str, Any] = {"close": float(row["close"])}
                            if "date" in df.columns:
                                rec["date"] = str(row["date"])
                            elif hasattr(idx, "strftime"):
                                rec["date"] = idx.strftime("%Y-%m-%d")
                            else:
                                rec["date"] = str(idx)
                            records.append(rec)
                        return records
                except Exception:
                    pass

        # Fallback to JSON fixture
        fixture_path = self._FIXTURE_DIR / f"{ticker}.json"
        if fixture_path.exists():
            try:
                with open(fixture_path) as f:
                    data = json.load(f)
                # Handle various JSON shapes
                if isinstance(data, list):
                    return self._normalize_series(data)
                if isinstance(data, dict):
                    for key in ("series", "ohlcv", "bars", "data"):
                        if key in data and isinstance(data[key], list):
                            return self._normalize_series(data[key])
                    # If it's a dict of {date: close} structure
                    records = []
                    for k, v in data.items():
                        if isinstance(v, (int, float)):
                            records.append({"date": k, "close": float(v)})
                        elif isinstance(v, dict) and "close" in v:
                            records.append({"date": k, "close": float(v["close"])})
                    return records
            except Exception:
                pass

        return []

    def _normalize_series(self, records: list) -> list[dict]:
        out = []
        for r in records:
            if isinstance(r, dict):
                close = r.get("close") or r.get("Close") or r.get("4. close")
                if close is not None:
                    entry: dict[str, Any] = {"close": float(close)}
                    for dk in ("date", "Date", "timestamp", "time"):
                        if dk in r:
                            entry["date"] = str(r[dk])
                            break
                    out.append(entry)
            elif isinstance(r, (int, float)):
                out.append({"close": float(r)})
        return out

    # ------------------------------------------------------------------ context builder

    def _build_context(self, ticker: str, close_prices: list[float]) -> dict:
        # Build a minimal context using local fallback forecast — no live API calls
        try:
            from forecasting_service import TimesFMForecastingService
            forecast = TimesFMForecastingService().forecast_from_prices(ticker, close_prices)
        except Exception:
            last = close_prices[-1] if close_prices else 100.0
            recent = close_prices[-5:] if len(close_prices) >= 5 else close_prices
            trend = (recent[-1] - recent[0]) / max(abs(recent[0]), 1e-9) if len(recent) > 1 else 0.0
            forecast = {
                "direction": "bullish" if trend > 0.005 else ("bearish" if trend < -0.005 else "neutral"),
                "confidence": min(0.95, 0.5 + abs(trend) * 8),
                "predicted_return": round(trend, 6),
                "trend_strength": round(abs(trend), 4),
                "forecast_deviation": 0.0,
            }

        # Minimal price features from close window
        rv5 = 0.0
        momentum = 0.0
        if len(close_prices) >= 5:
            returns = [(close_prices[i] - close_prices[i-1]) / max(abs(close_prices[i-1]), 1e-9)
                       for i in range(1, len(close_prices))]
            last5 = returns[-5:]
            if len(last5) > 1:
                try:
                    rv5 = round(stdev(last5) * math.sqrt(252), 4)
                except Exception:
                    rv5 = 0.0
            if len(close_prices) >= 10:
                momentum = round(
                    (close_prices[-1] - close_prices[-10]) / max(abs(close_prices[-10]), 1e-9),
                    4,
                )

        return {
            "timesfm": forecast,
            "divergence": {},
            "price": {
                "rolling_volatility_5d": rv5,
                "momentum": momentum,
                "rsi_14": 50.0,
            },
            "reddit": {},
            "news": {},
            "simulation": {"final_confidence": 0.5},
            "source_audit": {
                "ohlcv": {"status": "live"},
            },
        }

    # ------------------------------------------------------------------ option return

    def _compute_option_return(
        self,
        trade: str,
        actual_move: float,
        stop_loss_pct: float,
        take_profit_pct: float,
    ) -> float:
        leverage = 3.0
        slippage = 0.02

        if trade == "CALL":
            raw = actual_move * leverage if actual_move > 0 else -1.0
        elif trade == "PUT":
            raw = abs(actual_move) * leverage if actual_move < 0 else -1.0
        else:
            return 0.0

        # Apply caps
        raw = max(raw, -stop_loss_pct)
        raw = min(raw, take_profit_pct)
        return raw - slippage

    # ------------------------------------------------------------------ metrics

    def _compute_metrics(self, trades: list[dict]) -> dict:
        total = len(trades)
        if total == 0:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "avg_return": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
                "avg_hold_bars": 0.0,
                "by_strategy": {},
            }

        returns = [t["option_return"] for t in trades]
        winners = [r for r in returns if r > 0]
        losers = [r for r in returns if r <= 0]

        avg_return = round(mean(returns), 6)
        win_rate = round(len(winners) / total, 4)

        # Max drawdown via peak-to-trough on cumulative returns
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for r in returns:
            cum += r
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd
        max_drawdown = round(max_dd, 6)

        # Sharpe ratio
        if len(returns) > 1:
            try:
                std_r = stdev(returns)
                sharpe = round(avg_return / std_r * math.sqrt(252), 4) if std_r > 0 else 0.0
            except Exception:
                sharpe = 0.0
        else:
            sharpe = 0.0

        avg_hold = round(mean(t.get("bars_held", 1) for t in trades), 2)

        # By strategy
        by_strategy: dict[str, dict] = {}
        for t in trades:
            st = t.get("strategy_type", "unknown")
            if st not in by_strategy:
                by_strategy[st] = {"count": 0, "returns": []}
            by_strategy[st]["count"] += 1
            by_strategy[st]["returns"].append(t["option_return"])

        by_strategy_out = {}
        for st, v in by_strategy.items():
            st_returns = v["returns"]
            st_wins = sum(1 for r in st_returns if r > 0)
            by_strategy_out[st] = {
                "count": v["count"],
                "win_rate": round(st_wins / len(st_returns), 4) if st_returns else 0.0,
                "avg_return": round(mean(st_returns), 6) if st_returns else 0.0,
            }

        return {
            "total_trades": total,
            "winning_trades": len(winners),
            "losing_trades": len(losers),
            "win_rate": win_rate,
            "avg_return": avg_return,
            "max_drawdown": max_drawdown,
            "sharpe_ratio": sharpe,
            "avg_hold_bars": avg_hold,
            "by_strategy": by_strategy_out,
        }

    # ------------------------------------------------------------------ equity curve

    def _build_equity_curve(
        self, trades: list[dict], series: list[dict], window_size: int
    ) -> list[dict]:
        curve = []
        cum = 0.0
        trade_by_date: dict[str, float] = {t["date"]: t["option_return"] for t in trades}
        for i, bar in enumerate(series[window_size:], start=window_size):
            d = bar.get("date", str(i))
            if d in trade_by_date:
                cum += trade_by_date[d]
            curve.append({"date": d, "cumulative_return": round(cum, 6)})
        return curve

    # ------------------------------------------------------------------ persistence

    def _persist_results(
        self,
        ticker: str,
        horizon: str,
        metrics: dict,
        trades: list[dict],
        equity_curve: list[dict],
    ) -> dict:
        self._REPORT_DIR.mkdir(parents=True, exist_ok=True)
        today = date.today().strftime("%Y%m%d")
        base = f"{ticker}_{horizon}_{today}"

        json_path = self._REPORT_DIR / f"backtest_{base}.json"
        md_path = self._REPORT_DIR / f"backtest_{base}.md"
        trades_csv = self._REPORT_DIR / f"trades_{base}.csv"
        equity_csv = self._REPORT_DIR / f"equity_curve_{base}.csv"

        try:
            with open(json_path, "w") as f:
                json.dump({**metrics, "ticker": ticker, "horizon": horizon, "trades": trades}, f, indent=2)
        except Exception:
            pass

        try:
            with open(md_path, "w") as f:
                f.write(f"# Backtest: {ticker} ({horizon})\n\n")
                f.write(f"**Date:** {today}\n\n")
                f.write("## Summary\n\n")
                f.write(f"| Metric | Value |\n|---|---|\n")
                f.write(f"| Total Trades | {metrics['total_trades']} |\n")
                f.write(f"| Win Rate | {metrics['win_rate']:.1%} |\n")
                f.write(f"| Avg Return | {metrics['avg_return']:.4f} |\n")
                f.write(f"| Max Drawdown | {metrics['max_drawdown']:.4f} |\n")
                f.write(f"| Sharpe Ratio | {metrics['sharpe_ratio']:.4f} |\n\n")
                f.write("## By Strategy\n\n")
                for st, v in metrics.get("by_strategy", {}).items():
                    f.write(f"- **{st}**: {v['count']} trades, {v['win_rate']:.1%} win rate, {v['avg_return']:.4f} avg return\n")
        except Exception:
            pass

        try:
            if trades:
                with open(trades_csv, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=list(trades[0].keys()))
                    writer.writeheader()
                    writer.writerows(trades)
        except Exception:
            pass

        try:
            if equity_curve:
                with open(equity_csv, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=["date", "cumulative_return"])
                    writer.writeheader()
                    writer.writerows(equity_curve)
        except Exception:
            pass

        return {
            "json": str(json_path),
            "markdown": str(md_path),
            "trades_csv": str(trades_csv),
            "equity_curve_csv": str(equity_csv),
        }
