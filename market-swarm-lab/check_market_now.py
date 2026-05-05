#!/usr/bin/env python3
import sys, os
os.chdir('/Users/laxman_2026_mac_mini/.openclaw/workspace/market-swarm-lab')
sys.path.insert(0, 'services/backtest')
sys.path.insert(0, 'services/strategy-engine')
sys.path.insert(0, 'services/live_trading')
from bar_history import BarHistoryLoader
from datetime import datetime

loader = BarHistoryLoader()
tickers = ['SPY','QQQ','NVDA','AMD','MU','TSLA']

print(f'📊 Market Update: {datetime.now().strftime("%I:%M %p ET")}')
print('='*50)

for t in tickers:
    bars, src, cnt = loader.load_bars(t, days_back=5)
    if not bars or len(bars) < 20:
        print(f'{t}: NO DATA')
        continue
    
    ind, issues = loader.compute_indicators(bars)
    if not ind:
        print(f'{t}: {cnt} bars - ind not ready')
        continue
    
    price = ind.get('last', 0)
    ema9 = ind.get('ema9', 0)
    ema21 = ind.get('ema21', 0)
    rsi = ind.get('rsi14', 50)
    
    # Check for signal
    sig = 'HOLD'
    if price > ema9 * 1.001 and price > ema21 * 1.002:
        sig = '🟢 WATCH'
    elif price < ema9 * 0.999 and price < ema21 * 0.998:
        sig = '🔴 WATCH'
    
    print(f'{t}: ${price:.2f} | {sig} | RSI:{rsi:.1f} | EMA9:{ema9:.1f} | EMA21:{ema21:.1f} | src:{src}')
