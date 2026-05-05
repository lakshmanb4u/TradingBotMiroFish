# Token Optimization for Kimi Usage

## Problem
OpenRouter logs show 100k+ token requests. This is too expensive and unnecessary.

## Changes Implemented

### 1. Compact Context Builder
**File:** `services/llm_context/context_builder.py`

- Converts raw market data → structured summary
- NEVER includes:
  - Raw OHLCV arrays
  - Full logs
  - Full chat history
- ONLY includes:
  - Current price
  - VWAP / EMA / RSI
  - Volume ratio
  - Regime + TimesFM
  - Ensemble votes
  - Entry / stop / target
- Max size: input <8k tokens, output <500 tokens

### 2. Updated masi_agent.py
- Uses compact context builder for LLM prompts
- Added token validation before LLM calls
- Truncates prompts if >10k tokens
- Max output tokens: 500 (was 2048)
- LLM role restricted to confirm/veto/warn only
- Cannot generate trades or modify stops

### 3. Backtest LLM Disabled by Default
**File:** `services/backtest/point_in_time_replay.py`

- MASi confirmation disabled in backtests
- Backtests are pure Python by default
- Use `--use-masi` flag to enable (not recommended for batch)

### 4. Alert Scanner Updates
**File:** `mirofish_alerts.py`

- MASi disabled by default
- Use `--use-masi` flag to enable
- Shows token cost when MASi is called

### 5. Token Safety Limits
- Input >10k tokens → reject and log warning
- Input >20k tokens → fail request
- All LLM calls go through context_builder
- No direct raw data prompts allowed

## Expected Results
- Token usage: 120k → <8k (93% reduction)
- Cost reduction: 80-95%
- Faster response time
- Deterministic backtests by default

## Usage

### Default (no LLM):
```bash
python mirofish_signal.py backtest --ticker SPY
```

### With LLM confirmation (expensive):
```bash
python mirofish_alerts.py --use-masi
```

### Backtest with LLM (not recommended):
```bash
python mirofish_signal.py backtest --ticker SPY --use-masi
```
