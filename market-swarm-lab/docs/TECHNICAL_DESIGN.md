# market-swarm-lab — Technical Design Document

| | |
|---|---|
| **Status** | In Review |
| **Author** | Laxman Mamidi |
| **Created** | April 19, 2026 |
| **Last Updated** | April 19, 2026 |
| **Reviewers** | — |
| **Repo** | [github.com/lakshmanb4u/TradingBotMiroFish](https://github.com/lakshmanb4u/TradingBotMiroFish) |

---

## Table of Contents

1. [Overview](#1-overview)
2. [Problem Statement](#2-problem-statement)
3. [Goals and Non-Goals](#3-goals-and-non-goals)
4. [Background](#4-background)
5. [System Architecture](#5-system-architecture)
6. [Detailed Design](#6-detailed-design)
7. [Data Model](#7-data-model)
8. [API Design](#8-api-design)
9. [Signal Logic](#9-signal-logic)
10. [Risk Model](#10-risk-model)
11. [Storage](#11-storage)
12. [Operational Excellence](#12-operational-excellence)
13. [Security Considerations](#13-security-considerations)
14. [Performance](#14-performance)
15. [Alternatives Considered](#15-alternatives-considered)
16. [Rollout Plan](#16-rollout-plan)
17. [Open Questions](#17-open-questions)

---

## 1. Overview

**market-swarm-lab** is a local-first multi-agent market intelligence system that ingests live data from five independent sources, runs a simulation of 100 heterogeneous AI agents across four behavioral archetypes, detects cross-signal divergence, and produces actionable options trade signals for SPY and NVDA.

The system generates a `CALL`, `PUT`, or `HOLD` recommendation with a structured confidence score, position sizing guidance, and full audit trail of which data sources were live at time of decision.

All trade execution defaults to **paper mode**. Live brokerage execution requires explicit opt-in via two independent environment variables and is not implemented in the current version.

---

## 2. Problem Statement

### 2.1 Context

Retail options traders and quant hobbyists face two structural disadvantages:

1. **Signal fragmentation** — Price data, social sentiment, news, and prediction markets are consumed in isolation. No single tool fuses all four into a coherent view before generating a signal.

2. **Confidence blindness** — Signals are generated without an explicit measure of how much the underlying sources agree with each other. A model can be bullish while Reddit is bearish and Kalshi is neutral — without divergence detection, this ambiguity is invisible to the trader.

### 2.2 Opportunity

Cross-signal divergence is a well-documented precursor to price reversals in academic literature (e.g., sentiment-price divergence, prediction market vs. analyst disagreement). Retail systems that ignore inter-source divergence are systematically over-confident.

### 2.3 What We Are Building

A system that:
- Ingests five independent real-time signals
- Computes pairwise divergence across signals
- Seeds a heterogeneous agent swarm with source-appropriate context
- Generates signals only when cross-source alignment justifies it
- Produces a full explainability trail for every decision

---

## 3. Goals and Non-Goals

### Goals

- ✅ Ingest live OHLCV data from Alpha Vantage (100 days)
- ✅ Ingest Reddit sentiment via Apify (r/wallstreetbets, r/stocks, r/options)
- ✅ Ingest financial news via NewsAPI (last 3–5 days)
- ✅ Ingest Kalshi prediction market odds (no API key required)
- ✅ Ingest SEC filing risk scores via EDGAR
- ✅ Compute technical indicators: RSI-14, VWAP, rolling volatility (5d/10d), 10d momentum
- ✅ Run TimesFM 2.5 200M forecasting on real price history
- ✅ Detect cross-signal divergence (TimesFM vs Reddit vs Kalshi)
- ✅ Seed 100 agents across four behavioral archetypes with source-isolated context
- ✅ Generate CALL/PUT/HOLD signals with confidence, position sizing, and option plan
- ✅ Execute signals in paper mode with full order tracking
- ✅ Backtest strategy on historical OHLCV with win rate and Sharpe metrics
- ✅ Maintain portfolio state, trade journal, and PnL tracking
- ✅ Expose debug endpoints for each data source

### Non-Goals

- ❌ Live brokerage execution (out of scope for v1)
- ❌ Real options chain data (Phase 2 — requires Tradier or Polygon)
- ❌ Intraday (1-minute or 1-hour) OHLCV bars (Phase 2)
- ❌ Multi-leg option strategies (spreads, strangles)
- ❌ Portfolio optimization across multiple concurrent positions
- ❌ Mobile or web trading UI (Phase 3)

---

## 4. Background

### 4.1 TimesFM

[TimesFM 2.5](https://research.google/blog/timesfm-2-0-a-time-series-foundation-model-for-improved-predictions/) is a 200M parameter transformer-based time series foundation model released by Google in 2025. It accepts raw price sequences and outputs point forecasts and quantile predictions (p10/p50/p90) at configurable horizons. It requires no fine-tuning — zero-shot forecasting on arbitrary financial time series.

In market-swarm-lab, TimesFM is used to produce a 5-day price forecast from 60–100 days of close prices, mapped to a `bullish`, `bearish`, or `neutral` direction signal with a calibrated confidence score.

### 4.2 Apify Reddit Scraper

The [trudax/reddit-scraper](https://apify.com/trudax/reddit-scraper) Apify actor scrapes Reddit posts and comments without requiring Reddit OAuth credentials, bypassing Reddit's increasingly restrictive API tier limits. It returns structured post and comment data including scores, upvote ratios, and timestamps.

### 4.3 Divergence as a Market Signal

High divergence between technical models and crowd sentiment is a known precursor to price reversals. When TimesFM predicts bullish continuation but Reddit sentiment is strongly bearish and Kalshi markets are pricing a decline, this disagreement historically precedes regime change. Conversely, when all three signals align, trend continuation is statistically more likely.

---

## 5. System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        FastAPI (apps/api)                       │
│   /run-demo  /signal  /trade-plan  /backtest  /debug/*          │
└───────────────────────┬──────────────────────────────────────────┘
                        │
          ┌─────────────▼──────────────┐
          │      Collection Layer      │
          │  (5 independent services)  │
          └──────┬─────┬──────┬───────┘
                 │     │      │
     ┌───────────┘  ...│...   └───────────┐
     ▼                 ▼                  ▼
 price-collector  reddit-collector   news-collector
 (Alpha Vantage)  (Apify)           (NewsAPI)
     +                                    +
 sec-collector                     kalshi-collector
 (EDGAR)                           (Kalshi REST)
                        │
          ┌─────────────▼──────────────┐
          │   UnifiedNormalizerService  │
          │   → normalized_bundle{}     │
          └──────────────┬─────────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
   TimesFM          Divergence      Seed Builder
   Forecasting      Engine          (seed_pack)
          └──────────────┬──────────────┘
                         ▼
          ┌──────────────────────────────┐
          │    Agent Seeder (100 agents) │
          │  Retail / Institutional /    │
          │  Momentum / Contrarian       │
          └──────────────┬───────────────┘
                         ▼
          ┌──────────────────────────────┐
          │  Strategy Engine             │
          │  Risk Engine                 │
          └──────────────┬───────────────┘
                         ▼
          ┌──────────────────────────────┐
          │  Execution Engine (paper)    │
          │  Portfolio Engine            │
          │  Unified Reporter            │
          └──────────────────────────────┘
```

### 5.1 Component Summary

| Component | File(s) | Responsibility |
|---|---|---|
| price-collector | `alpha_vantage_client.py`, `price_service.py` | OHLCV fetch, RSI-14, VWAP, vol, momentum |
| reddit-collector | `apify_reddit_fetcher.py`, `apify_normalizer.py` | Reddit post/comment ingestion via Apify |
| news-collector | `newsapi_client.py`, `news_service.py` | News ingestion, theme extraction, sentiment |
| collector | `fetchers/kalshi.py`, `sec.py` | Prediction markets, SEC filings |
| normalizer | `normalizer_service.py` | Merge all sources into normalized_bundle |
| forecasting | `forecasting_service.py` | TimesFM 2.5 / local fallback |
| seed-builder | `seed_builder_service.py`, `divergence_engine.py` | seed_pack, divergence scores |
| agent-seeder | `agent_seeder_service.py` | Seed 100 agents, run simulation |
| strategy-engine | `strategy_engine_service.py` | Signal rules, option_plan |
| risk-engine | `risk_engine_service.py` | Confidence gates, position sizing |
| backtester | `backtester_service.py` | Historical signal replay |
| execution-engine | `execution_engine_service.py` | Paper order generation |
| portfolio-engine | `portfolio_engine_service.py` | PnL tracking, trade journal |
| reporting | `unified_reporter.py` | JSON + Markdown reports |

---

## 6. Detailed Design

### 6.1 Data Collection

Each collector follows the same interface contract:

```python
def collect(ticker: str) -> dict:
    # Returns:
    {
        "ticker": str,
        "provider_mode": str,    # "live" | "fallback"
        "source_audit": {...},
        # source-specific fields
    }
```

**Priority chains (in order of preference):**

```
OHLCV:   Alpha Vantage live  →  fixture_fallback
Reddit:  Apify live          →  Reddit OAuth live  →  fixture_fallback
News:    NewsAPI live        →  Alpha Vantage news →  fixture_fallback
Kalshi:  Kalshi REST live    →  fixture_fallback
SEC:     SEC API live        →  fixture_fallback
```

All fallbacks load from `infra/fixtures/{source}/{TICKER}.json`. Every collector sets `source_audit.{source}.status` to `"live"` or `"fallback"` so the caller always knows which path was taken.

### 6.2 Normalization

`UnifiedNormalizerService.normalize(ticker, raw_bundle)` merges all source data into a single `normalized_bundle` dict. Key fields written to `snapshot{}`:

- `close_prices[]`, `returns[]`, `volatility`, `rsi_14`, `momentum`, `vwap`, `price_trend`
- `reddit_sentiment`, `reddit_mentions`, `reddit_bullish_ratio`, `reddit_bearish_ratio`
- `kalshi_contracts[]`, `prediction_market_consensus`
- `sec_risk_score`

The `feature_window[]` (sliding window of daily snapshots) is used as input to the legacy TimesFM path.

### 6.3 Forecasting

Two execution paths:

**Path A — `forecast_from_prices(ticker, close_prices)` (primary)**
Accepts a raw list of 60–100 close prices. Used when `PriceService` has collected live data. Maps TimesFM direction to `bullish`/`bearish`/`neutral`. Computes `trend_strength` and `forecast_deviation`.

**Path B — `forecast(ticker, normalized_bundle)` (legacy)**
Reads from `normalized_bundle["feature_window"]`. Used when price data comes through the unified collector path.

Both paths fall back to a deterministic local extrapolation (linear trend + volatility bands) when TimesFM is not installed (`ENABLE_TIMESFM=false`).

### 6.4 Divergence Engine

```python
def compute_divergence(forecast, reddit_data, kalshi_data) -> dict:
```

Each signal is mapped to a numeric score on `[-1, +1]`:

| Signal | Mapping |
|---|---|
| TimesFM | `bullish=+1`, `neutral=0`, `bearish=-1` |
| Reddit | `bullish_ratio - bearish_ratio` (clamped to [-1, +1]) |
| Kalshi | avg YES prob: `>0.55 → +1`, `<0.45 → -1`, else `0` |

Pairwise divergence = `abs(score_A - score_B) / 2`, normalized to `[0, 1]`.

```
divergence_score = mean(timesfm_vs_reddit, timesfm_vs_kalshi, reddit_vs_kalshi)
alignment_score  = 1 - divergence_score

signal:
  alignment_score > 0.70  →  "trend_confirmation"
  divergence_score > 0.50 →  "reversal_candidate"
  else                    →  "mixed"
```

### 6.5 Agent Seeder

`seed_agents(seed, forecast, normalized_bundle)` creates 100 agents across four archetypes. Each agent receives only the data relevant to its behavioral role:

| Archetype | Count | Context Injected |
|---|---|---|
| Retail | 40 | `reddit_context{}`, `news_context{}` |
| Institutional | 30 | `timesfm_context{}`, `kalshi_context{}`, SEC risk |
| Momentum | 20 | `price_context{}` (RSI, vol, momentum, VWAP), `timesfm_context{}` |
| Contrarian | 10 | `divergence_context{}` (all pairwise scores + signal) |

`run_simulation()` aggregates the 100 agent votes into:
- `final_direction`: bullish / bearish / rangebound
- `buy_sell_ratio`: float
- `outlook_score`: float
- `final_confidence`: float
- `price_trajectory[]`: simulated daily prices
- `sentiment_per_day[]`: rolling sentiment and volatility

### 6.6 Strategy Engine

Signal generation follows a priority-ordered rule set. The first matching rule wins:

```
Rule 1: trend_confirmation_bullish
  IF timesfm.direction == "bullish"
  AND divergence.alignment_score >= 0.65
  AND divergence.divergence_score < 0.35
  THEN trade = CALL, strategy_type = "trend"

Rule 2: trend_confirmation_bearish
  IF timesfm.direction == "bearish"
  AND divergence.alignment_score >= 0.65
  AND divergence.divergence_score < 0.35
  THEN trade = PUT, strategy_type = "trend"

Rule 3: reversal_bullish
  IF reddit.bullish_ratio < 0.35  (crowd bearish)
  AND timesfm.direction == "bullish"
  AND divergence.signal == "reversal_candidate"
  THEN trade = CALL, strategy_type = "reversal"

Rule 4: reversal_bearish
  IF reddit.bullish_ratio > 0.65  (crowd very bullish)
  AND timesfm.direction == "bearish"
  AND divergence.signal == "reversal_candidate"
  THEN trade = PUT, strategy_type = "reversal"

Rule 5: volatility_play
  IF divergence.divergence_score > 0.6
  AND price.rolling_volatility_5d > 0.25
  THEN trade = HOLD, strategy_type = "volatility"

Rule 6: no_trade (default)
  THEN trade = HOLD, strategy_type = "no_trade"
```

**Confidence formula:**
```
confidence = (
    timesfm.confidence          × 0.35 +
    divergence.alignment_score  × 0.25 +
    simulation.final_confidence × 0.25 +
    source_freshness            × 0.15
)
```
where `source_freshness` = fraction of sources with `status == "live"`.

---

## 7. Data Model

### 7.1 normalized_bundle

```python
{
    "snapshot": {
        # Price
        "close_prices": list[float],
        "returns": list[float],
        "volatility": float,
        "rsi_14": float,
        "rolling_volatility_5d": float,
        "rolling_volatility_10d": float,
        "momentum": float,
        "vwap": float,
        "price_trend": "up" | "down" | "flat",
        # Reddit
        "reddit_sentiment": float,
        "reddit_mentions": int,
        "reddit_bullish_ratio": float,
        "reddit_bearish_ratio": float,
        "reddit_disagreement_index": float,
        # Kalshi
        "kalshi_contracts": list[dict],
        "prediction_market_consensus": float,
        # SEC
        "sec_risk_score": float,
    },
    "feature_window": list[dict],   # sliding daily snapshots
    "documents": list[dict],        # SEC filings
    "simulation_seed": dict,
    "timesfm_inputs": list[float],
    "reddit": dict,                 # full reddit_collector result
    "news": dict,                   # full news_service result
    "price": dict,                  # full price_service result
    "price_rich": dict,             # richer price_service result
    "timesfm": dict,                # forecast_from_prices result
    "divergence": dict,             # compute_divergence result
    "source_audit": {
        "ohlcv":  {"status", "provider", "record_count", "date_range"},
        "reddit": {"status", "provider", "record_count", "sample_post_titles"},
        "news":   {"status", "provider", "record_count", "sample_headlines"},
        "kalshi": {"status", "provider", "record_count", "sample_ids"},
        "sec":    {"status", "provider", "record_count", "sample_ids"},
    },
}
```

### 7.2 Signal Output

```python
{
    "ticker": str,
    "horizon": "1h" | "1d" | "3d" | "5d",
    "trade": "CALL" | "PUT" | "HOLD",
    "strategy_type": "trend" | "reversal" | "volatility" | "no_trade",
    "direction": "bullish" | "bearish" | "neutral",
    "confidence": float,           # 0.0–1.0
    "expected_move_pct": float,
    "reason": str,
    "drivers": list[str],
    "risk_flags": list[str],
    "option_plan": {
        "expiry_days": int,
        "strike_selection": str,
        "holding_period": str,
    },
    "entry_style": "marketable_limit" | "limit",
    "thesis": str,
}
```

### 7.3 Risk Output

```python
{
    "approved": bool,
    "position_size_pct": float,    # 0.0, 0.25, 0.50, or 0.75
    "stop_loss_pct": float,
    "take_profit_pct": float,
    "max_hold_time": str,
    "risk_notes": list[str],
    "adjusted_confidence": float,
}
```

---

## 8. API Design

All endpoints return JSON. Errors return `{"detail": str}` with appropriate HTTP status codes.

### GET /run-demo

Full pipeline run. All collection, intelligence, simulation, and signal steps.

**Query params:** `ticker` (default: `NVDA`)

**Response:**
```json
{
  "ticker": "SPY",
  "forecast": { "direction": "bullish", "confidence": 0.72, ... },
  "seed_pack": { "price_summary": "...", "timesfm_summary": "...", ... },
  "divergence": { "divergence_score": 0.22, "signal": "trend_confirmation", ... },
  "simulation": { "final_direction": "bullish", "buy_sell_ratio": 1.4, ... },
  "strategy_signal": { "trade": "CALL", "confidence": 0.68, ... },
  "risk_eval": { "approved": true, "position_size_pct": 0.50, ... },
  "trade_signal": { "trade": "CALL", ... },
  "source_audit": { "ohlcv": {...}, "reddit": {...}, "news": {...}, ... },
  "report": { ... }
}
```

### GET /signal

Single-step signal generation with risk evaluation.

**Query params:** `ticker`, `horizon` (default: `1d`)

### GET /trade-plan

Full flow: data collection → signal → risk → paper order.

**Query params:** `ticker`, `horizon`

### POST /backtest

Rolling 60-bar replay over stored OHLCV history.

**Query params:** `ticker`, `horizon`

### GET /debug/{source}

Source-specific debug endpoints. Each returns provider_mode, sample data, features, and source_audit.

---

## 9. Signal Logic

### 9.1 Option Plan by Horizon

| Horizon | DTE | Strike | Holding |
|---|---|---|---|
| 1h | 0 (same-day) | ATM | Intraday |
| 1d | 2 | ATM | Overnight |
| 3d | 4 | ATM+1% (call) / ATM-1% (put) | Multi-day |
| 5d | 7 | Delta-based | Multi-day |

### 9.2 Minimum Thresholds

- Confidence < 0.60 → always HOLD
- expected_move_pct < 0.3% → no trade opened
- OHLCV fallback → no trade approved

---

## 10. Risk Model

### 10.1 Position Sizing

| Confidence Band | Max Position Size |
|---|---|
| 0.60 – 0.70 | 25% |
| 0.70 – 0.80 | 50% |
| ≥ 0.80 | 75% |

### 10.2 Stop Loss / Take Profit

| Horizon | Stop Loss | Take Profit |
|---|---|---|
| 1h | 25% | 50% |
| 1d | 30% | 60% |
| 3d | 35% | 80% |
| 5d | 40% | 100% |

Values are applied as a percentage of option premium paid.

### 10.3 Risk Gates (in order)

1. `confidence < 0.60` → reject
2. `source_audit.ohlcv.status == "fallback"` → reject (live price required)
3. `source_audit.news.status == "fallback"` → reduce confidence by 0.05
4. `disagreement_index > 0.70` → reject unless strategy_type == "volatility"
5. `trade == "HOLD"` → position_size = 0, not executed

---

## 11. Storage

### 11.1 Directory Structure

```
state/
├── raw/
│   ├── ohlcv/          {TICKER}_{DATE}.json
│   │                   {TICKER}_timesfm_input_{DATE}.json
│   │                   {TICKER}_timesfm_output_{DATE}.json
│   ├── reddit/         {TICKER}_apify_{DATE}.json
│   └── news/           {TICKER}_{DATE}.json
├── orders/             {TICKER}_{timestamp}.json
├── portfolio/          trade_journal.json
│                       daily_summary_{date}.md
├── seeds/
├── cache/
└── reports/            {TICKER}_{DATE}.json
                        {TICKER}_{DATE}.md
                        backtest_{TICKER}_{horizon}_{DATE}.json
                        trades_{TICKER}_{horizon}_{DATE}.csv
                        equity_curve_{TICKER}_{horizon}_{DATE}.csv

data/
└── market_data/
    ├── ohlcv/          {TICKER}.parquet
    └── news/           {TICKER}.json
```

### 11.2 Optional Persistent Storage

| Store | Use |
|---|---|
| PostgreSQL | `run_reports` table — persists every pipeline run with ticker, score, paths |
| Redis | `market-swarm-lab:{TICKER}` key — 1-hour TTL cache of latest report |

Both are optional. The system runs fully without them.

---

## 12. Operational Excellence

### 12.1 Observability

- All source_audit fields are always populated and included in API responses
- `provider_mode` field on every collector result indicates live vs fallback
- Debug endpoints (`/debug/price`, `/debug/news`, `/debug/reddit`, `/debug/timesfm`) allow real-time inspection of each source independently

### 12.2 Failure Modes

| Failure | Behavior |
|---|---|
| Alpha Vantage API down | Load OHLCV fixture, set source_audit.ohlcv.status = "fallback", continue |
| Apify rate limited | Fall through to Reddit OAuth or fixture |
| NewsAPI quota exceeded | Fall through to Alpha Vantage news or fixture |
| TimesFM model not installed | Local deterministic fallback, same output schema |
| Kalshi API unreachable | Load fixture, confidence reduced |
| PostgreSQL unavailable | Skip persistence, log warning, continue |
| Redis unavailable | Skip caching, log warning, continue |

### 12.3 Logging

All services use Python `logging` module. Log warnings on fallback activation. No PII or API keys logged.

---

## 13. Security Considerations

### 13.1 Secret Management

- All API keys stored in `.env` (gitignored)
- `.env` never committed — enforced via `.gitignore`
- `state/` and `data/` directories gitignored — no raw API responses committed
- `.env.example` contains only placeholder values

### 13.2 Live Trading Guardrails

Live brokerage execution requires **both** of the following to be explicitly set:

```bash
EXECUTION_MODE=live
LIVE_TRADING_ENABLED=true
```

If either is missing or set to any other value, the execution engine defaults to `paper` mode. This is enforced at runtime in `execution_engine_service.py` — there is no code path that reaches live execution without both guards.

### 13.3 Input Validation

- All ticker inputs are uppercased before use
- All API responses are validated for expected keys before processing
- Fixture fallbacks prevent crashes on empty or malformed API responses

---

## 14. Performance

### 14.1 Latency Profile (estimated, paper mode)

| Step | Typical Latency |
|---|---|
| Alpha Vantage fetch (100 days) | 1–3s |
| Apify Reddit scraper | 15–60s (actor cold start) |
| NewsAPI fetch | < 1s |
| Kalshi fetch | < 1s |
| TimesFM inference (local) | 5–30s (GPU), 30–120s (CPU) |
| Local fallback forecast | < 100ms |
| Agent simulation (100 agents) | < 500ms |
| Strategy + risk evaluation | < 50ms |
| Total /run-demo (Apify path) | 30–90s |
| Total /run-demo (fixture path) | < 2s |

### 14.2 Optimization Notes

- Apify actor runs are the primary latency bottleneck. The actor can be pre-warmed or run on a schedule to cache results.
- TimesFM model loads lazily on first call and is cached in memory for subsequent calls.
- OHLCV Parquet files cached to `data/market_data/ohlcv/` — subsequent reads skip API call.
- Redis caching reduces repeated `/run-demo` latency to < 50ms when hot.

---

## 15. Alternatives Considered

### 15.1 Reddit API (Official) vs Apify

**Reddit OAuth API** was the original approach. Rejected because:
- Reddit's API terms now restrict bulk data access for commercial or ML use
- Rate limits (100 requests/min) constrain collection across multiple subreddits
- Credential management adds operational overhead

**Apify `trudax/reddit-scraper`** was chosen because it bypasses API rate limits, requires no Reddit credentials, and returns structured post + comment data including scores and timestamps.

### 15.2 GPT-4o vs TimesFM for Forecasting

**LLM-based forecasting** (GPT-4o with price history in context) was evaluated. Rejected because:
- LLMs are not calibrated for time series prediction
- Hallucination risk on numerical output is high
- Cost scales with context length (100 days of prices)

**TimesFM 2.5** was chosen because it is specifically designed for zero-shot time series forecasting, produces calibrated quantile outputs, and runs locally without API cost.

### 15.3 Single Monolithic Agent vs Agent Swarm

**Single LLM call** producing a trade signal directly was the simplest alternative. Rejected because:
- A single agent cannot model disagreement between behavioral archetypes
- The disagreement signal itself (contrarian vs retail vs institutional) is valuable information
- Swarm simulation produces a `final_confidence` that reflects genuine inter-agent disagreement

---

## 16. Rollout Plan

| Phase | Milestone | Status |
|---|---|---|
| **v0.1** | Multi-source collection + normalization | ✅ Complete |
| **v0.2** | TimesFM forecasting + divergence engine | ✅ Complete |
| **v0.3** | 100-agent simulation + seed builder | ✅ Complete |
| **v0.4** | Strategy engine + risk engine | ✅ Complete |
| **v0.5** | Backtester (Phase 1 approximation) | ✅ Complete |
| **v0.6** | Paper execution + portfolio tracking | ✅ Complete |
| **v0.7** | Real options chain (Tradier/Polygon) | 🔜 Planned |
| **v0.8** | Intraday OHLCV (1h bars) | 🔜 Planned |
| **v0.9** | Live execution (gated) | 🔜 Planned |
| **v1.0** | Web dashboard + alerting | 🔜 Planned |

---

## 17. Open Questions

| # | Question | Owner | Status |
|---|---|---|---|
| 1 | Which broker API for live execution? (Tradier, Alpaca, IBKR) | — | Open |
| 2 | Should Apify Reddit runs be scheduled (cron) vs on-demand? | — | Open |
| 3 | What win rate threshold on backtest is required before live execution is enabled? | — | Open |
| 4 | Should TimesFM run on a dedicated GPU instance or CPU-only? | — | Open |
| 5 | Should the system support multi-leg options (spreads, iron condors)? | — | Open |
| 6 | What is the target paper-trading track record length before live is considered? | — | Open |
