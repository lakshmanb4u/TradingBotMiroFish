# Multi-Stage Trade Management Implementation

## Overview
Implement multi-stage trade management with partial profit-taking to reduce variance while preserving trend-following upside.

## Components Implemented

### 1. TradeState Class
- Tracks position size (% remaining)
- Records partial exits
- Calculates PnL in R terms
- Tracks MFE/MAE

### 2. MultiStageManager Class
- Processes bars and checks for exits
- Implements partial profit-taking:
  - Stage 1 (+1R): Close 50%, stop→breakeven
  - Stage 2 (+2R): Close 25%
  - Stage 3 (+3R): Trail remaining 25%
- Configurable trailing stops (ATR, EMA, swing)

### 3. MetricsCalculator Class
- expectancy
- median trade outcome
- skew
- equity smoothness
- % profit from top 3 trades

### 4. Comparison Function
- Compare full-runner vs partial-profit models
- Calculate differences in metrics

## Integration Steps

### Step 1: Update Trade Class
- Add partial exit tracking
- Add strategy parameter
- Add realized_pnl_r field

### Step 2: Update TradeSimulator
- Import MultiStageManager
- Add strategy parameter to open_trade
- Process bars through manager

### Step 3: Update BacktestEngine
- Add strategy parameter
- Run both models for comparison

### Step 4: Update ReportWriter
- Include partial exit details
- Calculate new metrics

## Usage

```python
# Create manager
manager = MultiStageManager(
    strategy="partial_profit",
    trail_type="atr",
    trail_params={"atr_mult": 2.0}
)

# Process trade
exits = manager.process_bar(trade, bar, indicators)
```

## Testing Plan
1. Run SPY backtest with full_runner
2. Run SPY backtest with partial_profit
3. Compare metrics
4. Test on multiple tickers
