#!/usr/bin/env python3
"""
Compare 4 full-runner exit management strategies:
1. full_runner_current - exit at target_1 or stop
2. full_runner_atr_trail - trail stop using 1.0 ATR after +1R
3. full_runner_ema_trail - trail using EMA9/EMA21 break after +1R
4. full_runner_time_stop - exit if not +1R within N bars

Does NOT modify entries. Only exit logic varies.
"""

import sys
import os
os.chdir('/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab')
sys.path.insert(0, 'services/backtest')
sys.path.insert(0, 'services/strategy-engine')
sys.path.insert(0, 'services/agent-seeder')

from point_in_time_replay import (
    Bar, BarStream, IndicatorState, UWContextLoader,
    EnsembleAdapter, RegimeAdapter, TimesFMAdapter,
    VoteLogger, load_thresholds
)
from datetime import date, datetime, timedelta
import statistics
import json
from pathlib import Path

# ─── Trade class with exit strategy support ─────────────────────────

class Trade:
    def __init__(self, signal, entry_bar, regime, uw_bias, tf_dir, masi, strategy="full_runner_current"):
        self.signal_ts = entry_bar.ts
        self.entry_ts = entry_bar.ts
        self.entry_price = entry_bar.open
        self.action = signal["action"]
        self.target_1 = signal.get("target_1", 0)
        self.target_2 = signal.get("target_2", 0)
        
        raw_stop = signal.get("stop_loss", 0)
        min_stop_dist = self.entry_price * 0.001
        if self.action == "BUY":
            self.stop_loss = min(raw_stop, self.entry_price - min_stop_dist)
        else:
            self.stop_loss = max(raw_stop, self.entry_price + min_stop_dist)
        
        self.ticker = signal.get("ticker", "?")
        self.regime = regime
        self.uw_bias = uw_bias
        self.timesfm_direction = tf_dir
        self.masi_verdict = masi
        self.confidence = signal.get("confidence", "?")
        self.votes_bull = signal.get("votes_bull", 0)
        self.votes_bear = signal.get("votes_bear", 0)
        self.strategy = strategy
        
        self.exit_ts = None
        self.exit_price = 0.0
        self.exit_reason = ""
        self.pnl_pts = 0.0
        self.pnl_r = 0.0
        self.mfe = 0.0
        self.mae = 0.0
        self.open = True
        self.bars_held = 0
        
        # Trailing stop state
        self.trailing_stop = None
        self.in_profit_zone = False
        self.highest_price = self.entry_price if self.action == "BUY" else self.entry_price
        self.lowest_price = self.entry_price if self.action == "SELL/SHORT" else self.entry_price
    
    @property
    def risk_pts(self):
        return abs(self.entry_price - self.stop_loss)
    
    def update_excursion(self, bar):
        if self.action == "BUY":
            self.mfe = max(self.mfe, bar.high - self.entry_price)
            self.mae = max(self.mae, self.entry_price - bar.low)
            self.highest_price = max(self.highest_price, bar.high)
        else:
            self.mfe = max(self.mfe, self.entry_price - bar.low)
            self.mae = max(self.mae, bar.high - self.entry_price)
            self.lowest_price = min(self.lowest_price, bar.low)
    
    def close(self, exit_bar, reason):
        self.exit_ts = exit_bar.ts
        self.exit_price = exit_bar.close if reason == "eod" else (
            exit_bar.open if reason in ("stop", "target", "trail", "time") else exit_bar.close
        )
        self.exit_reason = reason
        self.pnl_pts = (self.exit_price - self.entry_price) if self.action == "BUY" \
                       else (self.entry_price - self.exit_price)
        self.pnl_r = max(-1.0, min(self.pnl_pts / self.risk_pts if self.risk_pts > 0 else 0.0, 999.0))
        self.open = False


# ─── Trade Simulator with 4 exit strategies ─────────────────────────

class TradeSimulator:
    def __init__(self, strategy="full_runner_current", time_stop_bars=6):
        self._open = []
        self._closed = []
        self.strategy = strategy
        self.time_stop_bars = time_stop_bars
        self._last_signal_ts = {}
    
    @property
    def all_trades(self):
        return self._closed + self._open
    
    def can_open(self, ticker, action, now):
        last = self._last_signal_ts.get(ticker)
        if last and (now - last).total_seconds() < 3600:
            return False
        return len(self._open) < 1
    
    def open_trade(self, signal, entry_bar, regime, uw_bias, tf_dir, masi):
        trade = Trade(signal, entry_bar, regime, uw_bias, tf_dir, masi, strategy=self.strategy)
        self._open.append(trade)
        self._last_signal_ts[entry_bar.symbol] = entry_bar.ts
        return trade
    
    def _check_stop(self, trade, bar):
        """Check if stop hit (intrabar)."""
        if trade.action == "BUY" and bar.low <= trade.stop_loss:
            fill = min(trade.stop_loss, bar.open) if bar.open < trade.stop_loss else trade.stop_loss
            trade.close(bar, "stop")
            trade.exit_price = fill
            trade.pnl_pts = trade.exit_price - trade.entry_price
            trade.pnl_r = max(-1.0, trade.pnl_pts / trade.risk_pts) if trade.risk_pts > 0 else 0.0
            return True
        if trade.action == "SELL/SHORT" and bar.high >= trade.stop_loss:
            fill = max(trade.stop_loss, bar.open) if bar.open > trade.stop_loss else trade.stop_loss
            trade.close(bar, "stop")
            trade.exit_price = fill
            trade.pnl_pts = trade.entry_price - trade.exit_price
            trade.pnl_r = max(-1.0, trade.pnl_pts / trade.risk_pts) if trade.risk_pts > 0 else 0.0
            return True
        return False
    
    def _check_target(self, trade, bar):
        """Check if target_1 hit."""
        if trade.action == "BUY" and bar.high >= trade.target_1:
            trade.close(bar, "target_1")
            return True
        if trade.action == "SELL/SHORT" and bar.low <= trade.target_1:
            trade.close(bar, "target_1")
            return True
        return False
    
    def _check_atr_trail(self, trade, bar, indicators):
        """Trail stop using 1.0 ATR after +1R reached."""
        atr = indicators.get("atr14", trade.entry_price * 0.005)
        
        # Check if in profit zone (+1R)
        if not trade.in_profit_zone:
            if trade.action == "BUY" and bar.high >= trade.entry_price + trade.risk_pts:
                trade.in_profit_zone = True
                trade.trailing_stop = bar.close - atr
            elif trade.action == "SELL/SHORT" and bar.low <= trade.entry_price - trade.risk_pts:
                trade.in_profit_zone = True
                trade.trailing_stop = bar.close + atr
            return False
        
        # Update trailing stop
        if trade.action == "BUY":
            new_stop = bar.close - atr
            if new_stop > trade.trailing_stop:
                trade.trailing_stop = new_stop
            if bar.low <= trade.trailing_stop:
                fill = min(trade.trailing_stop, bar.open) if bar.open < trade.trailing_stop else trade.trailing_stop
                trade.close(bar, "trail")
                trade.exit_price = fill
                trade.pnl_pts = trade.exit_price - trade.entry_price
                trade.pnl_r = max(-1.0, trade.pnl_pts / trade.risk_pts) if trade.risk_pts > 0 else 0.0
                return True
        else:
            new_stop = bar.close + atr
            if new_stop < trade.trailing_stop:
                trade.trailing_stop = new_stop
            if bar.high >= trade.trailing_stop:
                fill = max(trade.trailing_stop, bar.open) if bar.open > trade.trailing_stop else trade.trailing_stop
                trade.close(bar, "trail")
                trade.exit_price = fill
                trade.pnl_pts = trade.entry_price - trade.exit_price
                trade.pnl_r = max(-1.0, trade.pnl_pts / trade.risk_pts) if trade.risk_pts > 0 else 0.0
                return True
        return False
    
    def _check_ema_trail(self, trade, bar, indicators):
        """Trail using EMA break after +1R reached."""
        ema9 = indicators.get("ema9", 0)
        ema21 = indicators.get("ema21", 0)
        
        if not trade.in_profit_zone:
            if trade.action == "BUY" and bar.high >= trade.entry_price + trade.risk_pts:
                trade.in_profit_zone = True
            elif trade.action == "SELL/SHORT" and bar.low <= trade.entry_price - trade.risk_pts:
                trade.in_profit_zone = True
            return False
        
        # Exit on EMA break (price crosses below EMA9 for longs)
        if trade.action == "BUY":
            if bar.low < ema9 and bar.close < ema9:
                fill = min(ema9, bar.open) if bar.open < ema9 else ema9
                trade.close(bar, "ema_trail")
                trade.exit_price = fill
                trade.pnl_pts = trade.exit_price - trade.entry_price
                trade.pnl_r = max(-1.0, trade.pnl_pts / trade.risk_pts) if trade.risk_pts > 0 else 0.0
                return True
        else:
            if bar.high > ema9 and bar.close > ema9:
                fill = max(ema9, bar.open) if bar.open > ema9 else ema9
                trade.close(bar, "ema_trail")
                trade.exit_price = fill
                trade.pnl_pts = trade.entry_price - trade.exit_price
                trade.pnl_r = max(-1.0, trade.pnl_pts / trade.risk_pts) if trade.risk_pts > 0 else 0.0
                return True
        return False
    
    def _check_time_stop(self, trade, bar):
        """Exit if trade doesn't reach +1R within N bars."""
        trade.bars_held += 1
        
        # If reached +1R, cancel time stop
        if trade.action == "BUY" and bar.high >= trade.entry_price + trade.risk_pts:
            return False
        if trade.action == "SELL/SHORT" and bar.low <= trade.entry_price - trade.risk_pts:
            return False
        
        # Time stop
        if trade.bars_held >= self.time_stop_bars:
            trade.close(bar, "time")
            return True
        return False
    
    def update(self, bar, indicators=None):
        closed_this_bar = []
        still_open = []
        
        for trade in self._open:
            trade.update_excursion(bar)
            
            # Always check stop first
            if self._check_stop(trade, bar):
                self._closed.append(trade)
                closed_this_bar.append(trade)
                continue
            
            # Strategy-specific exit logic
            if self.strategy == "full_runner_current":
                if self._check_target(trade, bar):
                    self._closed.append(trade)
                    closed_this_bar.append(trade)
                    continue
                    
            elif self.strategy == "full_runner_atr_trail":
                if self._check_atr_trail(trade, bar, indicators or {}):
                    self._closed.append(trade)
                    closed_this_bar.append(trade)
                    continue
                    
            elif self.strategy == "full_runner_ema_trail":
                if self._check_ema_trail(trade, bar, indicators or {}):
                    self._closed.append(trade)
                    closed_this_bar.append(trade)
                    continue
                    
            elif self.strategy == "full_runner_time_stop":
                if self._check_time_stop(trade, bar):
                    self._closed.append(trade)
                    closed_this_bar.append(trade)
                    continue
            
            still_open.append(trade)
        
        self._open = still_open
        return closed_this_bar
    
    def close_eod(self, bar):
        for trade in self._open:
            trade.close(bar, "eod")
            trade.exit_price = bar.open
            trade.pnl_pts = (trade.exit_price - trade.entry_price) if trade.action == "BUY" \
                           else (trade.entry_price - trade.exit_price)
            trade.pnl_r = max(-1.0, min(trade.pnl_pts / trade.risk_pts, 999.0)) if trade.risk_pts > 0 else 0.0
            self._closed.append(trade)
        self._open = []


# ─── Backtest Engine (simplified) ───────────────────────────────────

def run_strategy(strategy_name, time_stop_bars=6):
    """Run backtest with specific exit strategy."""
    print(f"\n{'='*60}")
    print(f"Running: {strategy_name}")
    print(f"{'='*60}")
    
    ticker = "SPY"
    start = date(2026, 4, 1)
    end = date(2026, 4, 25)
    freq_min = 5
    
    # Load bars
    stream = BarStream(ticker, start, end, freq_min)
    print(f"  Bars: {stream.bar_count} from {stream.source}")
    
    # Setup
    indicators = IndicatorState()
    ensemble = EnsembleAdapter()
    regime_adapter = RegimeAdapter()
    uw_loader = UWContextLoader(ticker)
    tf_adapter = TimesFMAdapter()
    sim = TradeSimulator(strategy=strategy_name, time_stop_bars=time_stop_bars)
    
    thresholds = load_thresholds("loose")
    
    # Daily bars for regime
    daily_bars = []
    
    signals_fired = 0
    
    for bar, history in stream.stream():
        indicators.update(history)
        ind = indicators.as_dict()
        
        # Update daily bars
        day_key = bar.ts.date().isoformat()
        if not daily_bars or daily_bars[-1].get("date") != day_key:
            daily_bars.append({
                "date": day_key,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            })
        else:
            daily_bars[-1]["high"] = max(daily_bars[-1]["high"], bar.high)
            daily_bars[-1]["low"] = min(daily_bars[-1]["low"], bar.low)
            daily_bars[-1]["close"] = bar.close
            daily_bars[-1]["volume"] += bar.volume
        
        # Regime
        regime = regime_adapter.get_regime(bar.ts.date(), daily_bars)
        
        # UW context
        uw_ctx, _ = uw_loader.get_context(bar.ts)
        
        # Ensemble score
        ens = ensemble.score(ind, uw_ctx, bar, history)
        
        # TimesFM
        tf = tf_adapter.get(bar.ts.date(), indicators.closes())
        
        # Update open trades
        sim.update(bar, ind)
        
        # Check for new signal
        action = ens.get("action", "HOLD")
        if action == "HOLD":
            continue
        
        votes_bull = ens.get("votes_bull", 0)
        votes_bear = ens.get("votes_bear", 0)
        
        # Threshold check
        if action == "BUY" and votes_bull < thresholds["min_votes_bull"]:
            continue
        if action == "SELL/SHORT" and votes_bear < thresholds["min_votes_bear"]:
            continue
        
        # Cooldown
        if not sim.can_open(ticker, action, bar.ts):
            continue
        
        # Open trade
        trade = sim.open_trade(ens, bar, regime.get("regime", "CHOP"), 
                              uw_ctx.get("flow_bias", "neutral"),
                              tf.get("direction", "neutral"), "NOT_CALLED")
        signals_fired += 1
    
    # Close any open trades at end
    if sim._open:
        last_bar = history[-1] if history else bar
        sim.close_eod(last_bar)
    
    print(f"  Signals: {signals_fired}")
    print(f"  Closed trades: {len(sim._closed)}")
    
    return sim._closed


# ─── Analysis ───────────────────────────────────────────────────────

def analyze_trades(trades, name):
    if not trades:
        return {"name": name, "total_trades": 0}
    
    pnls = [t.pnl_r for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    
    total = sum(pnls)
    win_rate = len(wins) / len(pnls) * 100 if pnls else 0
    
    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0.01
    profit_factor = gross_profit / gross_loss if gross_loss else 999
    
    # Max drawdown
    equity = [0]
    for p in pnls:
        equity.append(equity[-1] + p)
    peak = equity[0]
    max_dd = 0
    for e in equity:
        if e > peak:
            peak = e
        dd = peak - e
        if dd > max_dd:
            max_dd = dd
    
    # Hold time
    hold_times = []
    for t in trades:
        if t.exit_ts and t.entry_ts:
            mins = (t.exit_ts - t.entry_ts).total_seconds() / 60
            hold_times.append(mins)
    avg_hold = statistics.mean(hold_times) if hold_times else 0
    
    # Top 3 contribution
    sorted_pnls = sorted(pnls, reverse=True)
    top3 = sum(sorted_pnls[:3])
    top3_pct = (top3 / total * 100) if total != 0 else 0
    
    # Expectancy
    win_pct = len(wins) / len(trades) if trades else 0
    avg_win = statistics.mean(wins) if wins else 0
    avg_loss = statistics.mean(losses) if losses else 0
    expectancy = (win_pct * avg_win) + ((1 - win_pct) * avg_loss)
    
    # Median
    median_r = statistics.median(pnls) if pnls else 0
    
    return {
        "name": name,
        "total_trades": len(trades),
        "win_rate": win_rate,
        "total_r": total,
        "profit_factor": profit_factor,
        "max_dd": max_dd,
        "expectancy": expectancy,
        "median_r": median_r,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "best": max(pnls),
        "worst": min(pnls),
        "top3_pct": top3_pct,
        "avg_hold_min": avg_hold,
    }


# ─── Main ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    strategies = [
        ("full_runner_current", 6),
        ("full_runner_atr_trail", 6),
        ("full_runner_ema_trail", 6),
        ("full_runner_time_stop", 6),
    ]
    
    results = {}
    for strategy, ts_bars in strategies:
        trades = run_strategy(strategy, ts_bars)
        results[strategy] = analyze_trades(trades, strategy)
    
    # Print comparison
    print("\n" + "="*70)
    print("EXIT STRATEGY COMPARISON")
    print("="*70)
    
    metrics = [
        ("Total Trades", "total_trades", ".0f"),
        ("Win Rate %", "win_rate", ".1f"),
        ("Total R", "total_r", ".3f"),
        ("Profit Factor", "profit_factor", ".2f"),
        ("Max DD R", "max_dd", ".2f"),
        ("Expectancy R", "expectancy", ".3f"),
        ("Median R", "median_r", ".3f"),
        ("Avg Win R", "avg_win", ".3f"),
        ("Avg Loss R", "avg_loss", ".3f"),
        ("Best Trade R", "best", ".3f"),
        ("Worst Trade R", "worst", ".3f"),
        ("Top 3 Contrib %", "top3_pct", ".1f"),
        ("Avg Hold min", "avg_hold_min", ".1f"),
    ]
    
    # Header
    names = [s[0].replace("full_runner_", "") for s in strategies]
    print(f"\n{'Metric':<18}", end="")
    for n in names:
        print(f"{n:>14}", end="")
    print()
    print("-" * 74)
    
    # Rows
    for label, key, fmt in metrics:
        print(f"{label:<18}", end="")
        for strategy, _ in strategies:
            val = results[strategy].get(key, 0)
            print(f"{val:>14{fmt}}", end="")
        print()
    
    # Best outlier preservation
    print("\n" + "="*70)
    print("OUTLIER PRESERVATION RANKING")
    print("="*70)
    
    sorted_by_best = sorted(results.items(), key=lambda x: x[1]["best"], reverse=True)
    for i, (name, data) in enumerate(sorted_by_best, 1):
        short = name.replace("full_runner_", "")
        print(f"{i}. {short:<20} Best: +{data['best']:.2f}R  Total: +{data['total_r']:.2f}R")
    
    print("\n" + "="*70)
    print("VERDICT")
    print("="*70)
    
    best_total = max(results.items(), key=lambda x: x[1]["total_r"])
    best_pf = max(results.items(), key=lambda x: x[1]["profit_factor"])
    best_dd = min(results.items(), key=lambda x: x[1]["max_dd"])
    
    print(f"🏆 Best Total Return: {best_total[0].replace('full_runner_', '')} (+{best_total[1]['total_r']:.2f}R)")
    print(f"🏆 Best Profit Factor: {best_pf[0].replace('full_runner_', '')} ({best_pf[1]['profit_factor']:.2f})")
    print(f"🏆 Lowest Drawdown: {best_dd[0].replace('full_runner_', '')} ({best_dd[1]['max_dd']:.2f}R)")
