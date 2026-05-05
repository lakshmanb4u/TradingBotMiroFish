# MiroFish Live Trading - Setup Guide

## ✅ What's Built

### 1. Configuration (`config/live_trading_config.json`)
- **Mode**: alert_only (default), paper, or live
- **Tickers**: SPY, QQQ, NVDA
- **Risk Controls**: max 3 trades/day, 0.5% risk per trade
- **Safety**: Human confirmation required, live orders DISABLED

### 2. Live Signal Engine (`mirofish_live.py`)
**Commands:**
```bash
# Alert mode (default) - generates signals, sends alerts
python mirofish_live.py --mode alert_only --once

# Paper trading - simulates trades, tracks PnL
python mirofish_live.py --mode paper

# With debug server
python mirofish_live.py --mode alert_only --debug-server --debug-port 8765

# Single ticker
python mirofish_live.py --ticker SPY --once
```

### 3. Alert Format
```json
{
  "ticker": "SPY",
  "action": "BUY_CALL",
  "underlying_entry": 711.83,
  "underlying_stop": 709.50,
  "exit_strategy": "full_runner",
  "confidence": 78,
  "regime": "BULL",
  "votes": "3/4",
  "reason": "Ensemble signal: BUY with 3/0 votes",
  "risk_notes": ["ATR-based stop: $709.50", "Target 1: $714.20"],
  "human_confirmation_required": true,
  "option_contract": {
    "symbol": "SPY20260430C71200000",
    "strike": 712.0,
    "expiry": "2026-04-30",
    "premium": 2.45,
    "delta": 0.35
  }
}
```

### 4. Paper Trading (`services/live_trading/paper_trader.py`)
**Features:**
- Simulates option fills
- Tracks open positions
- Tracks realized/unrealized PnL
- Writes to:
  - `state/live/paper_positions.json`
  - `state/live/trade_journal.csv`
  - `state/live/daily_summary_YYYY-MM-DD.md`

### 5. Options Selector (`services/live_trading/options_selector.py`)
**Rules:**
- Default 1DTE, max 2DTE
- Preferred delta: 0.30-0.40
- Min volume: 100
- Min open interest: 500
- Max spread: 10%
- Validates risk budget

### 6. Debug Endpoint (`services/live_trading/debug_endpoint.py`)
**Access:**
```bash
# Text status
curl http://localhost:8765/debug/live-trading

# Or run with server
python mirofish_live.py --debug-server
```

**Returns:**
- Current mode
- Active tickers
- Last signal
- Open paper positions
- Kimi token usage
- Source audit

### 7. Risk Rules (Hard-coded)
- ✅ Max 3 trades per day
- ✅ Max 0.5% account risk per trade
- ✅ No averaging down
- ✅ No martingale
- ✅ No live orders unless `allow_live_orders=true`
- ✅ Human confirmation required by default

## 🚫 What's NOT Enabled

1. **Live Schwab Order Placement**
   - `allow_live_orders: false` (hard-coded safety)
   - No order execution code exists

2. **MASi/Kimi Confirmation**
   - Framework exists but not wired to live flow yet
   - Currently deterministic signals only

3. **WhatsApp Alerts**
   - Placeholder in code
   - Needs OpenClaw messaging integration

## 📁 File Structure

```
market-swarm-lab/
├── config/
│   └── live_trading_config.json
├── mirofish_live.py
├── services/
│   └── live_trading/
│       ├── alert_formatter.py
│       ├── options_selector.py
│       ├── paper_trader.py
│       └── debug_endpoint.py
└── state/
    └── live/
        ├── live_trading.log
        ├── paper_positions.json
        ├── trade_journal.csv
        └── daily_summary_YYYY-MM-DD.md
```

## 🚀 Next Steps

1. **Test during market hours** (9:30 AM - 4:00 PM ET)
2. **Verify alerts** are generated correctly
3. **Add WhatsApp integration** for alert delivery
4. **Paper trade** for 1-2 weeks to validate
5. **Add MASi confirmation** before execution
6. **Only then** consider live orders (Phase 3)

## ⚠️ Safety Checklist

Before enabling ANY live trading:
- [ ] Paper trade for 2+ weeks profitably
- [ ] Verify all alerts are correct
- [ ] Test human confirmation flow
- [ ] Set `allow_live_orders: true` manually
- [ ] Start with 1 contract only
- [ ] Monitor every trade closely
