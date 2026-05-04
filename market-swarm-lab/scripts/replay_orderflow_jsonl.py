#!/usr/bin/env python3
"""
Deterministic Replay Engine for Bookmap Orderflow JSONL

Consumes captured Bookmap events and reconstructs orderflow analytics:
- Footprint candles (bid/ask delta per price level)
- Sweep detection (aggressive orders hitting liquidity)
- Absorption & stacked imbalance
- Liquidity pulls / reclaim

Generates Reddit-style signals:
  LONG:  sell sweep into support + delta divergence
  SHORT: buy sweep into resistance + exhaustion

Reports: trades.csv, equity_curve.csv, sweep_events.csv,
         footprint_events.csv, replay_report.md
"""

import json, gzip, csv, argparse, math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ─── Configuration ──────────────────────────────────────────────────────────

DEFAULT_BAR_SECS = 30
SWEEP_SIZE_THRESHOLD = 20       # min contracts for sweep flag

STOP_TICKS = 16                 # 16-tick hard stop
TARGET_TICKS = 32               # 32-tick target (2:1 RR)
TIME_EXIT_SECONDS = 120         # max hold time
MAX_RISK_PER_TRADE = 100.0      # $ risk per trade (for sizing)

# Per-symbol specs  (keyed by substring match in symbol)
SYMBOLS = {
    'ES':   {'tick_size': 0.25, 'tick_value': 12.50,  'comm': 5.00, 'name': 'E-mini S&P'},
    'MES':  {'tick_size': 0.25, 'tick_value': 1.25,   'comm': 2.50, 'name': 'Micro ES'},
    'NQ':   {'tick_size': 0.25, 'tick_value': 5.00,   'comm': 5.00, 'name': 'E-mini NQ'},
    'MNQ':  {'tick_size': 0.25, 'tick_value': 0.50,   'comm': 2.50, 'name': 'Micro NQ'},
    'GC':   {'tick_size': 0.10, 'tick_value': 10.00,  'comm': 5.00, 'name': 'Gold'},
    'MGC':  {'tick_size': 0.10, 'tick_value': 1.00,   'comm': 2.50, 'name': 'Micro Gold'},
    'BTC':  {'tick_size': 5.00, 'tick_value': 5.00,   'comm': 0.00, 'name': 'Bitcoin'},
    'ETH':  {'tick_size': 0.01, 'tick_value': 0.01,   'comm': 0.00, 'name': 'Ethereum'},
    'CL':   {'tick_size': 0.01, 'tick_value': 10.00,  'comm': 5.00, 'name': 'Crude Oil'},
    'MCL':  {'tick_size': 0.01, 'tick_value': 1.00,   'comm': 2.50, 'name': 'Micro Crude'},
}

def _sym_cfg(symbol: str) -> dict:
    sym_uc = symbol.upper()
    for k, v in SYMBOLS.items():
        if k in sym_uc:
            return v
    return SYMBOLS['ES']          # sane default

# ─── Data Classes ───────────────────────────────────────────────────────────

@dataclass(slots=True)
class DepthEvent:
    ts: datetime
    symbol: str
    price: float
    size: int
    side: str          # 'bid' | 'ask'

@dataclass(slots=True)
class TradeEvent:
    ts: datetime
    symbol: str
    price: float
    size: int
    aggressor: str     # 'buy' | 'sell'

@dataclass
class FootprintBar:
    symbol: str
    open_ts: datetime
    close_ts: datetime
    open_p: float
    high_p: float
    low_p: float
    close_p: float
    bid_vol: Dict[float, int] = field(default_factory=lambda: defaultdict(int))
    ask_vol: Dict[float, int] = field(default_factory=lambda: defaultdict(int))
    delta: int = 0
    total_vol: int = 0

@dataclass(slots=True)
class SweepEvent:
    ts: datetime
    symbol: str
    price: float
    size: int
    sweep_type: str    # 'sell_sweep' | 'buy_sweep'
    context: str

@dataclass(slots=True)
class Trade:
    entry_ts: datetime
    exit_ts: Optional[datetime]
    symbol: str
    direction: str     # 'LONG' | 'SHORT'
    entry_price: float
    exit_price: Optional[float]
    contracts: int
    pnl: float
    mae: float         # in ticks
    mfe: float         # in ticks
    exit_reason: str
    signal: str
    trailing_stop: float = 0.0   # 0 = not active

@dataclass(slots=True)
class Signal:
    ts: datetime
    symbol: str
    direction: str
    price: float
    reason: str

# ─── Replay Engine ──────────────────────────────────────────────────────────

class OrderflowReplay:
    def __init__(self, bar_secs: int = DEFAULT_BAR_SECS):
        self.bar_secs = bar_secs
        self.events: List[DepthEvent | TradeEvent] = []
        self.bars: Dict[str, List[FootprintBar]] = defaultdict(list)
        self.cur_bar: Dict[str, Optional[FootprintBar]] = {}
        self.ob: Dict[str, Dict[str, Dict[float, int]]] = defaultdict(
            lambda: {"bid": {}, "ask": {}}
        )
        self.sweeps: List[SweepEvent] = []
        self.signals: List[Signal] = []
        self.trades: List[Trade] = []
        self.equity: List[Tuple[datetime, float]] = []
        self.trade_hist: Dict[str, deque] = defaultdict(lambda: deque(maxlen=200))

    # ── Load ────────────────────────────────────────────────────────────────
    def load_jsonl(self, path: Path) -> None:
        opener = gzip.open if str(path).endswith('.gz') else open
        n = 0
        with opener(path, 'rt') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = rec.get('event_type')
                ts = self._parse_ts(rec.get('ts_event') or rec.get('ts_recv'))
                sym = rec.get('symbol', '')
                if etype == 'depth':
                    p = rec.get('price')
                    if p is None:
                        continue
                    self.events.append(DepthEvent(
                        ts=ts, symbol=sym, price=float(p),
                        size=int(rec.get('size', 0)), side=rec.get('side', 'bid')))
                    n += 1
                elif etype == 'trade':
                    p = rec.get('price')
                    if p is None:
                        continue
                    agg = 'sell' if (rec.get('side') == 'sell' or rec.get('is_bid_aggressor')) else 'buy'
                    self.events.append(TradeEvent(
                        ts=ts, symbol=sym, price=float(p),
                        size=int(rec.get('size', 0)), aggressor=agg))
                    n += 1
        self.events.sort(key=lambda e: e.ts)
        print(f"Loaded {n:,} events from {path.name}")

    def _parse_ts(self, s):
        if s.endswith('Z'):
            s = s[:-1] + '+00:00'
        return datetime.fromisoformat(s)

    # ── Replay ──────────────────────────────────────────────────────────────
    # ── Replay (Correct Execution) ────────────────────────────────────────
    def replay(self, start_ts=None, end_ts=None):
        """Event-driven replay with proper stop/target/time evaluation."""
        self._stats = {'stop_hit': 0, 'target_hit': 0, 'time_exit': 0,
                       'trailing_stop': 0, 'total_trades': 0}
        equity = 0.0
        open_trade: Optional[Trade] = None

        for ev in self.events:
            if start_ts and ev.ts < start_ts:
                continue
            if end_ts and ev.ts > end_ts:
                break

            # Update book / bars regardless
            if isinstance(ev, DepthEvent):
                self._on_depth(ev)
            elif isinstance(ev, TradeEvent):
                self._on_trade(ev)
                if ev.size >= SWEEP_SIZE_THRESHOLD:
                    self._detect_sweep(ev)

            # ━━ Evaluate position management on EVERY event ━━
            if open_trade:
                if ev.symbol != open_trade.symbol:
                    # Symbol mismatch — skip
                    continue

                # Update MAE/MFE on trade events only (depth levels are not market prices)
                if isinstance(ev, TradeEvent):
                    self._update_mae_mfe(open_trade, ev)

                exit_result = self._evaluate_exit(open_trade, ev)
                if exit_result:
                    open_trade.exit_ts = ev.ts
                    open_trade.exit_price = exit_result['fill_price']
                    open_trade.exit_reason = exit_result['reason']
                    open_trade.pnl = self._pnl(open_trade)
                    self.trades.append(open_trade)
                    equity += open_trade.pnl
                    self.equity.append((ev.ts, equity))
                    # Stat collection
                    self._stats['total_trades'] += 1
                    if exit_result['reason'] == 'stop':
                        self._stats['stop_hit'] += 1
                    elif exit_result['reason'] == 'target':
                        self._stats['target_hit'] += 1
                    elif exit_result['reason'] == 'time':
                        self._stats['time_exit'] += 1
                    elif exit_result['reason'] == 'trailing_stop':
                        self._stats['trailing_stop'] += 1
                    open_trade = None

            # ━━ Signal detection on trades only (entry occurs on next trade event) ━━
            if not open_trade and isinstance(ev, TradeEvent) and ev.symbol != '':
                sig = self._check_signal(ev)
                if sig:
                    cfg = _sym_cfg(ev.symbol)
                    contracts = max(1, int(MAX_RISK_PER_TRADE / (STOP_TICKS * cfg['tick_value'])))
                    open_trade = Trade(
                        entry_ts=ev.ts, exit_ts=None, symbol=ev.symbol,
                        direction=sig.direction, entry_price=ev.price,
                        exit_price=None, contracts=contracts,
                        pnl=0.0, mae=0.0, mfe=0.0, exit_reason='', signal=sig.reason)
                    self.signals.append(sig)

        # Close remaining at last trade of same symbol
        if open_trade:
            last_price = open_trade.entry_price
            last_ts = open_trade.entry_ts
            for rev_ev in reversed(self.events):
                if isinstance(rev_ev, TradeEvent) and rev_ev.symbol == open_trade.symbol:
                    last_price = rev_ev.price
                    last_ts = rev_ev.ts
                    break
            open_trade.exit_ts = last_ts
            open_trade.exit_price = last_price
            open_trade.exit_reason = 'replay_end'
            open_trade.pnl = self._pnl(open_trade)
            self.trades.append(open_trade)
            if self.equity:
                self.equity.append((last_ts, self.equity[-1][1] + open_trade.pnl))
            else:
                self.equity.append((last_ts, open_trade.pnl))

    def _get_best_bid_ask(self, sym: str) -> Tuple[float, float]:
        """Return (best_bid, best_ask) from current orderbook."""
        ob_sym = self.ob.get(sym, {'bid': {}, 'ask': {}})
        bids = sorted(ob_sym.get('bid', {}).keys(), reverse=True)
        asks = sorted(ob_sym.get('ask', {}).keys())
        best_bid = bids[0] if bids else 0.0
        best_ask = asks[0] if asks else 999999.0
        return best_bid, best_ask

    def _evaluate_exit(self, tr: Trade, ev) -> Optional[dict]:
        """
        Return {'reason': str, 'fill_price': float} if trade should exit, else None.
        Evaluated on every event (depth + trade). Uses book-aware fills.
        """
        cfg = _sym_cfg(tr.symbol)
        tsz = cfg['tick_size']
        now = ev.ts

        # Update trailing stop first
        self._update_trailing(tr, ev)

        best_bid, best_ask = self._get_best_bid_ask(tr.symbol)

        # ━━ TIME EXIT (check every event) ━━
        if (now - tr.entry_ts).total_seconds() >= TIME_EXIT_SECONDS:
            # Use mid for time exit fill
            fill = (best_bid + best_ask) / 2 if best_bid and best_ask < 999999 else \
                   (ev.price if isinstance(ev, TradeEvent) else tr.entry_price)
            return {'reason': 'time', 'fill_price': fill}

        # ━━ STOP & TARGET (bid/ask-aware) ━━
        if tr.direction == 'LONG':
            stop_level = tr.entry_price - STOP_TICKS * tsz
            tgt_level = tr.entry_price + TARGET_TICKS * tsz

            # For long: stop fills if trade THROUGH best_bid <= stop_level
            # or if trade price itself is below stop (aggressive stop-loss sweep)
            if isinstance(ev, TradeEvent):
                if ev.price <= stop_level:
                    fill = max(ev.price, best_bid) if best_bid else ev.price
                    return {'reason': 'stop', 'fill_price': fill}
                if ev.price >= tgt_level:
                    fill = min(ev.price, best_ask) if best_ask < 999999 else ev.price
                    return {'reason': 'target', 'fill_price': fill}
            else:
                # Depth update: if best_bid drops through stop, we hit stop on next trade
                if best_bid <= stop_level:
                    return {'reason': 'stop', 'fill_price': best_bid}
                if best_ask >= tgt_level:
                    return {'reason': 'target', 'fill_price': best_ask}

            # Trailing stop — only evaluate on trade events (market price)
            if isinstance(ev, TradeEvent) and tr.trailing_stop > 0.0 and ev.price <= tr.trailing_stop:
                return {'reason': 'trailing_stop', 'fill_price': ev.price}

        else:  # SHORT
            stop_level = tr.entry_price + STOP_TICKS * tsz
            tgt_level = tr.entry_price - TARGET_TICKS * tsz

            if isinstance(ev, TradeEvent):
                if ev.price >= stop_level:
                    fill = min(ev.price, best_ask) if best_ask < 999999 else ev.price
                    return {'reason': 'stop', 'fill_price': fill}
                if ev.price <= tgt_level:
                    fill = max(ev.price, best_bid) if best_bid else ev.price
                    return {'reason': 'target', 'fill_price': fill}
                if tr.trailing_stop > 0.0 and ev.price >= tr.trailing_stop:
                    return {'reason': 'trailing_stop', 'fill_price': ev.price}
            else:
                if best_ask >= stop_level:
                    return {'reason': 'stop', 'fill_price': best_ask}
                if best_bid <= tgt_level:
                    return {'reason': 'target', 'fill_price': best_bid}

        return None

    def _update_trailing(self, tr: Trade, ev):
        """Update trailing stop at breakeven (1:1) then trail 0.5R behind MFE.
        Only evaluated on trade events (market prices). Depth events are stale levels."""
        if not isinstance(ev, TradeEvent):
            return
        cfg = _sym_cfg(tr.symbol)
        tsz = cfg['tick_size']

        # Current MFE in ticks relative to entry
        if tr.direction == 'LONG':
            mfe_ticks = (ev.price - tr.entry_price) / tsz
        else:
            mfe_ticks = (tr.entry_price - ev.price) / tsz

        # Only trail when profitable by at least 1:1
        if mfe_ticks < STOP_TICKS:
            return

        # Activate breakeven on first 1:1 profit
        if tr.trailing_stop == 0.0:
            tr.trailing_stop = tr.entry_price

        # Trail: move stop toward favorable direction, never reverse
        trail_buffer = 0.5 * STOP_TICKS * tsz
        if tr.direction == 'LONG':
            new_stop = ev.price - trail_buffer
            if new_stop > tr.trailing_stop:
                tr.trailing_stop = new_stop
        else:
            new_stop = ev.price + trail_buffer
            if new_stop < tr.trailing_stop:
                tr.trailing_stop = new_stop

    def _on_depth(self, ev: DepthEvent):
        side = self.ob[ev.symbol][ev.side]
        if ev.size == 0:
            side.pop(ev.price, None)
        else:
            side[ev.price] = ev.size
        bar = self._bar(ev.symbol, ev.ts, ev.price)
        if ev.side == 'bid':
            bar.bid_vol[ev.price] = ev.size
        else:
            bar.ask_vol[ev.price] = ev.size

    def _on_trade(self, ev: TradeEvent):
        bar = self._bar(ev.symbol, ev.ts, ev.price)
        if ev.aggressor == 'buy':
            bar.ask_vol[ev.price] += ev.size
            bar.delta += ev.size
        else:
            bar.bid_vol[ev.price] += ev.size
            bar.delta -= ev.size
        bar.total_vol += ev.size
        bar.close_p = ev.price
        bar.high_p = max(bar.high_p, ev.price)
        bar.low_p  = min(bar.low_p,  ev.price)
        self.trade_hist[ev.symbol].append(ev)

    def _bar(self, sym, ts, price) -> FootprintBar:
        bar_ts = ts.replace(second=(ts.second // self.bar_secs) * self.bar_secs, microsecond=0)
        b = self.cur_bar.get(sym)
        if b is None or bar_ts >= b.close_ts:
            b = FootprintBar(
                symbol=sym, open_ts=bar_ts,
                close_ts=bar_ts + timedelta(seconds=self.bar_secs),
                open_p=price, high_p=price, low_p=price, close_p=price)
            self.cur_bar[sym] = b
            self.bars[sym].append(b)
        return b

    # ── Signal Detection ────────────────────────────────────────────────────
    def _detect_sweep(self, ev: TradeEvent):
        ob = self.ob.get(ev.symbol, {})
        if ev.aggressor == 'buy':
            asks = sorted(ob.get('ask', {}).keys())
            if asks and ev.price >= min(asks):
                self.sweeps.append(SweepEvent(
                    ts=ev.ts, symbol=ev.symbol, price=ev.price, size=ev.size,
                    sweep_type='buy_sweep', context=f'buy_{ev.size}@'))
        else:
            bids = sorted(ob.get('bid', {}).keys(), reverse=True)
            if bids and ev.price <= max(bids):
                self.sweeps.append(SweepEvent(
                    ts=ev.ts, symbol=ev.symbol, price=ev.price, size=ev.size,
                    sweep_type='sell_sweep', context=f'sell_{ev.size}@'))

    def _check_signal(self, ev: TradeEvent) -> Optional[Signal]:
        sym = ev.symbol
        ob = self.ob.get(sym, {})
        hist = list(self.trade_hist.get(sym, deque()))
        if len(hist) < 10:
            return None

        bids = sorted(ob.get('bid', {}).keys(), reverse=True)
        asks = sorted(ob.get('ask', {}).keys())
        if not bids or not asks:
            return None

        recent = [t for t in hist if (ev.ts - t.ts).total_seconds() <= 60]
        if not recent:
            return None
        delta = sum(t.size if t.aggressor == 'buy' else -t.size for t in recent)

        # ── LONG ──────────────────────────────────────────────────────────
        if (ev.aggressor == 'sell' and ev.size >= SWEEP_SIZE_THRESHOLD
                and delta < -50 and len(bids) >= 3):
            sz = sum(ob['bid'].get(p, 0) for p in bids[:3]) / 3
            if sz > 20:
                return Signal(ts=ev.ts, symbol=sym, direction='LONG',
                              price=ev.price,
                              reason=f'sweep_support_div_')

        if (ev.aggressor == 'buy' and delta > 30
                and len(self.bars.get(sym, [])) >= 3):
            lows = [b.low_p for b in self.bars[sym][-3:]]
            if len(lows) >= 2 and ev.price > lows[-2]:
                return Signal(ts=ev.ts, symbol=sym, direction='LONG',
                              price=ev.price, reason='reclaim')

        # ── SHORT ─────────────────────────────────────────────────────────
        if (ev.aggressor == 'buy' and ev.size >= SWEEP_SIZE_THRESHOLD
                and delta > 50 and len(asks) >= 3):
            sz = sum(ob['ask'].get(p, 0) for p in asks[:3]) / 3
            if sz > 20:
                return Signal(ts=ev.ts, symbol=sym, direction='SHORT',
                              price=ev.price,
                              reason=f'sweep_resist_exhaust_')

        if (ev.aggressor == 'sell' and delta < -30
                and len(self.bars.get(sym, [])) >= 3):
            highs = [b.high_p for b in self.bars[sym][-3:]]
            if len(highs) >= 2 and ev.price < highs[-2]:
                return Signal(ts=ev.ts, symbol=sym, direction='SHORT',
                              price=ev.price, reason='failed_break')

        return None

    def _update_mae_mfe(self, tr: Trade, ev):
        """Update MAE/MFE on any price event (trade or depth)."""
        cfg = _sym_cfg(tr.symbol)
        tsz = cfg['tick_size']
        price = ev.price
        if tr.direction == 'LONG':
            mae_ticks = (tr.entry_price - price) / tsz
            mfe_ticks = (price - tr.entry_price) / tsz
        else:
            mae_ticks = (price - tr.entry_price) / tsz
            mfe_ticks = (tr.entry_price - price) / tsz
        tr.mae = max(tr.mae, mae_ticks)
        tr.mfe = max(tr.mfe, mfe_ticks)

    def _should_exit(self, tr: Trade, ev: TradeEvent) -> bool:
        cfg = _sym_cfg(tr.symbol)
        tsz = cfg['tick_size']
        if tr.direction == 'LONG':
            if ev.price <= tr.entry_price - STOP_TICKS * tsz:
                tr.exit_reason = 'stop'; return True
            if ev.price >= tr.entry_price + TARGET_TICKS * tsz:
                tr.exit_reason = 'target'; return True
        else:
            if ev.price >= tr.entry_price + STOP_TICKS * tsz:
                tr.exit_reason = 'stop'; return True
            if ev.price <= tr.entry_price - TARGET_TICKS * tsz:
                tr.exit_reason = 'target'; return True
        if (ev.ts - tr.entry_ts).total_seconds() >= TIME_EXIT_SECONDS:
            tr.exit_reason = 'time'; return True
        return False

    def _pnl(self, tr: Trade) -> float:
        if tr.exit_price is None:
            return 0.0
        cfg = _sym_cfg(tr.symbol)
        tsz = cfg['tick_size']; tv = cfg['tick_value']
        if tr.direction == 'LONG':
            ticks = (tr.exit_price - tr.entry_price) / tsz
        else:
            ticks = (tr.entry_price - tr.exit_price) / tsz
        return ticks * tv * tr.contracts - cfg['comm'] * tr.contracts

    # ── Reports ─────────────────────────────────────────────────────────────
    def export(self, out_dir: Path):
        out_dir.mkdir(parents=True, exist_ok=True)
        self._csv_trades(out_dir / 'trades.csv')
        self._csv_equity(out_dir / 'equity_curve.csv')
        self._csv_sweeps(out_dir / 'sweep_events.csv')
        self._csv_footprint(out_dir / 'footprint_events.csv')
        self._md_report(out_dir / 'replay_report.md')
        print(f"Reports → {out_dir}")

    def _csv_trades(self, p: Path):
        with open(p, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['entry_ts','exit_ts','symbol','direction','entry_price',
                        'exit_price','contracts','pnl','mae','mfe','exit_reason','signal'])
            for t in self.trades:
                w.writerow([
                    t.entry_ts.isoformat(), t.exit_ts.isoformat() if t.exit_ts else '',
                    t.symbol, t.direction, f'{t.entry_price:.2f}',
                    f'{t.exit_price:.2f}' if t.exit_price else '',
                    t.contracts, f'{t.pnl:.2f}', f'{t.mae:.2f}', f'{t.mfe:.2f}',
                    t.exit_reason, t.signal])

    def _csv_equity(self, p: Path):
        with open(p, 'w', newline='') as f:
            w = csv.writer(f); w.writerow(['timestamp','equity'])
            for ts, eq in self.equity:
                w.writerow([ts.isoformat(), f'{eq:.2f}'])

    def _csv_sweeps(self, p: Path):
        with open(p, 'w', newline='') as f:
            w = csv.writer(f); w.writerow(['ts','symbol','price','size','sweep_type','context'])
            for s in self.sweeps:
                w.writerow([s.ts.isoformat(), s.symbol, f'{s.price:.2f}',
                            s.size, s.sweep_type, s.context])

    def _csv_footprint(self, p: Path):
        with open(p, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['open_ts','symbol','open','high','low','close','delta','total_vol'])
            for sym, bars in self.bars.items():
                for b in bars:
                    w.writerow([b.open_ts.isoformat(), sym,
                                f'{b.open_p:.2f}', f'{b.high_p:.2f}',
                                f'{b.low_p:.2f}', f'{b.close_p:.2f}',
                                b.delta, b.total_vol])

    def _md_report(self, p: Path):
        tr = self.trades; n = len(tr)
        if n == 0:
            return
        winners = [t for t in tr if t.pnl > 0]
        losers  = [t for t in tr if t.pnl <= 0]
        wn, ln = len(winners), len(losers)
        wr = wn / n * 100
        avg_w = sum(t.pnl for t in winners) / wn if wn else 0
        avg_l = sum(t.pnl for t in losers)  / ln if ln else 0
        gross = sum(t.pnl for t in tr)
        exp = (wr/100 * avg_w) + ((1-wr/100) * avg_l)
        rr = abs(avg_w / avg_l) if avg_l else 0

        peak = running = 0.0; max_dd = 0.0
        for t in tr:
            running += t.pnl
            if running > peak: peak = running
            dd = peak - running
            if dd > max_dd: max_dd = dd

        avg_mae = sum(t.mae for t in tr) / n
        avg_mfe = sum(t.mfe for t in tr) / n
        fbr = sum(1 for t in tr if 'failed_break' in t.signal or t.mae > 3) / n * 100

        md = f"""# Orderflow Replay Report
Generated: {datetime.now().isoformat()}
Events: {len(self.events):,} | Symbols: {', '.join(self.bars.keys())}

## Performance
| Metric | Value |
|--------|-------|
| Total Trades | {n} |
| Win Rate | {wr:.1f}% |
| Gross PnL | ${gross:,.2f} |
| Expectancy | ${exp:.2f} |
| R:R | {rr:.2f}:1 |
| Max Drawdown | ${max_dd:,.2f} |
| Avg MAE | {avg_mae:.1f} ticks |
| Avg MFE | {avg_mfe:.1f} ticks |
| False Breakout Rate | {fbr:.1f}% |

## Distribution
- Winners: {wn} (${sum(t.pnl for t in winners):,.2f})
- Losers:  {ln} (${sum(t.pnl for t in losers):,.2f})
- Avg Win: ${avg_w:.2f} | Avg Loss: ${avg_l:.2f}

## Signals
"""
        sigs = defaultdict(lambda: {'LONG':0,'SHORT':0,'pnl':0.0})
        for t in tr:
            sigs[t.signal][t.direction] += 1
            sigs[t.signal]['pnl'] += t.pnl
        for s, d in sorted(sigs.items()):
            md += f"- **{s}**: {d['LONG']}L / {d['SHORT']}S | ${d['pnl']:,.2f}\n"

        md += f"\n## Sweeps\nTotal: {len(self.sweeps)}\n"
        md += f"- Sell sweeps: {len([s for s in self.sweeps if s.sweep_type=='sell_sweep'])}\n"
        md += f"- Buy sweeps:  {len([s for s in self.sweeps if s.sweep_type=='buy_sweep'])}\n"

        # Execution breakdown
        md += "\n## Execution Breakdown\n"
        stats = getattr(self, '_stats', {})
        md += f"- **Stop hit**: {stats.get('stop_hit', 0)} ({stats.get('stop_hit',0)/n*100:.0f}%)\n"
        md += f"- **Target hit**: {stats.get('target_hit', 0)} ({stats.get('target_hit',0)/n*100:.0f}%)\n"
        md += f"- **Time exit**: {stats.get('time_exit', 0)} ({stats.get('time_exit',0)/n*100:.0f}%)\n"
        md += f"- **Trailing stop**: {stats.get('trailing_stop', 0)} ({stats.get('trailing_stop',0)/n*100:.0f}%)\n"
        md += f"- **Replay end**: {n - sum(stats.get(k,0) for k in ['stop_hit','target_hit','time_exit','trailing_stop'])}\n"

        # Execution validation assertions
        md += "\n## Execution Validation\n"
        for t in tr:
            if t.exit_reason == 'stop':
                md += f"✓ STOP: {t.symbol} {t.direction} entry={t.entry_price:.2f} exit={t.exit_price:.2f} mae={t.mae:.1f}t\n"
            elif t.exit_reason == 'target':
                md += f"✓ TARGET: {t.symbol} {t.direction} entry={t.entry_price:.2f} exit={t.exit_price:.2f} mfe={t.mfe:.1f}t\n"
            elif t.exit_reason == 'time':
                md += f"✗ TIMEOUT: {t.symbol} {t.direction} entry={t.entry_price:.2f} exit={t.exit_price:.2f} mae={t.mae:.1f}t mfe={t.mfe:.1f}t\n"
            elif t.exit_reason == 'trailing_stop':
                md += f"✓ TRAIL: {t.symbol} {t.direction} entry={t.entry_price:.2f} exit={t.exit_price:.2f}\n"
            else:
                md += f"? UNK: {t.symbol} {t.direction} reason={t.exit_reason}\n"

        # session breakdown
        md += "\n## Sessions\n"
        sess = defaultdict(list)
        for t in tr:
            sess[t.entry_ts.strftime('%Y-%m-%d %H')].append(t)
        for k, ts in sorted(sess.items()):
            md += f"- **{k}**: {len(ts)} trades, ${sum(t.pnl for t in ts):,.2f}\n"

        p.write_text(md)

# ─── CLI ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description='Bookmap Orderflow Replay Engine')
    ap.add_argument('-i', '--input', required=True, help='JSONL file or glob')
    ap.add_argument('-o', '--output', default='state/orderflow/replay_results', help='Output dir')
    ap.add_argument('--bar-secs', type=int, default=DEFAULT_BAR_SECS)
    ap.add_argument('--start', help='ISO start timestamp')
    ap.add_argument('--end', help='ISO end timestamp')
    args = ap.parse_args()

    eng = OrderflowReplay(bar_secs=args.bar_secs)
    p = Path(args.input)
    if '*' in str(p):
        import glob
        files = sorted(glob.glob(str(p)))
    elif p.is_dir():
        files = sorted(p.glob('*.jsonl'))
    else:
        files = [p]

    for f in files:
        eng.load_jsonl(Path(f))

    start = datetime.fromisoformat(args.start) if args.start else None
    end   = datetime.fromisoformat(args.end)   if args.end   else None

    print(f"Replaying {len(eng.events):,} events...")
    eng.replay(start_ts=start, end_ts=end)
    eng.export(Path(args.output))

    print("\n" + "="*50)
    print(f"Trades: {len(eng.trades)} | Signals: {len(eng.signals)} | Sweeps: {len(eng.sweeps)}")
    if eng.trades:
        pnl = sum(t.pnl for t in eng.trades)
        wr = len([t for t in eng.trades if t.pnl > 0]) / len(eng.trades) * 100
        print(f"Win Rate: {wr:.1f}% | Total PnL: ${pnl:,.2f}")
    print("="*50)

if __name__ == '__main__':
    main()
