# Technical Design Document — market-swarm-lab

**Version:** 1.0  
**Last Updated:** April 2026  
**Repo:** [github.com/lakshmanb4u/TradingBotMiroFish](https://github.com/lakshmanb4u/TradingBotMiroFish)

---

## 1. Goal

Build a **local-first, multi-agent market intelligence system** that:

1. Ingests live data from five independent sources (price, social sentiment, news, prediction markets, SEC filings)
2. Detects **signal alignment and divergence** across sources using a dedicated divergence engine
3. Runs a **swarm of 100 AI agents** — each seeded with source-specific context — to simulate realistic retail, institutional, momentum, and contrarian market behavior
4. Generates **actionable option trade signals** (CALL / PUT / HOLD) with confidence scores, position sizing, and risk parameters
5. Executes trades in **paper mode by default**, with a strict guardrail system before any live execution is permitted
6. Maintains a **full audit trail** — every run records which sources were live, what each agent received, and what the final decision was

The system is designed to be:
- **Explainable** — every signal has a reason, every agent has a context, every source has an audit entry
- **Resilient** — every data source has a fixture fallback; no pipeline step fails silently
- **Extensible** — new sources, agent archetypes, or strategies can be added without touching existing services

---

## 2. Target Instruments

| Symbol | Description |
|---|---|
| **SPY** | S&P 500 ETF — primary signal target |
| **NVDA** | NVIDIA — high-beta, sentiment-driven |

Both support options trading with high liquidity, making them suitable for the ATM and near-ATM option strategies the system targets.

---

## 3. Frameworks & Technology Stack

### Core Runtime

| Layer | Technology | Reason |
|---|---|---|
| API server | **FastAPI** + Uvicorn | Async, typed, fast; OpenAPI docs auto-generated |
| HTTP clients | **httpx** | Async-capable, timeout control, used across all fetchers |
| Data processing | **pandas** + **pyarrow** | Parquet output for OHLCV, vectorized feature computation |
| Containerization | **Docker** + Docker Compose | Reproducible local and cloud deployment |
| Language | **Python 3.11+** | Type hints, match statements, modern stdlib |

### ML / Forecasting

| Component | Technology | Notes |
|---|---|---|
| Price forecasting | **TimesFM 2.5 200M** (Google, PyTorch) | Optional; loaded lazily; falls back to deterministic trend extrapolation |
| Sentiment NLP | Custom keyword-based scorer (`nlp.py`) | No external model dependency; positive/negative word set scoring |
| RSI, volatility, momentum | Pure Python (`math`, `statistics`) | No numpy dependency in core services |

### Data Sources

| Source | API | Auth |
|---|---|---|
| OHLCV | Alpha Vantage `TIME_SERIES_DAILY_ADJUSTED` | API key |
| Reddit | Apify `trudax/reddit-scraper` | API token |
| News | NewsAPI `everything` endpoint | API key |
| Prediction markets | Kalshi REST API | No key (read-only) |
| SEC filings | SEC EDGAR / SEC API | Optional key |

### Storage

| Store | Format | Path |
|---|---|---|
| Raw API responses | JSON | `state/raw/{source}/` |
| Normalized OHLCV | Parquet | `data/market_data/ohlcv/` |
| Normalized news | JSON | `data/market_data/news/` |
| Order tickets | JSON | `state/orders/` |
| Portfolio journal | JSON + Markdown | `state/portfolio/` |
| Run reports | JSON + Markdown | `state/reports/` |
| Backtest output | JSON + CSV + Markdown | `state/reports/` |

### Optional Infrastructure

| Component | Technology | When needed |
|---|---|---|
| Run report persistence | **PostgreSQL** | Production deployments |
| Report caching | **Redis** | High-frequency repeated queries |
| MiroFish bridge | REST (internal) | When MiroFish simulation engine is running |

---

## 4. System Design Principles

### 4.1 Separation of Concerns

Each service has one job:
- **Collectors** fetch and normalize raw data — they don't interpret it
- **Intelligence layer** interprets normalized data — it doesn't fetch anything
- **Agent seeder** distributes context to agents — it doesn't compute signals
- **Strategy engine** computes signals — it doesn't execute anything
- **Execution engine** executes — it doesn't compute signals

### 4.2 Graceful Degradation

Every external dependency has a fallback:

```
Alpha Vantage live  →  fixture_fallback
Apify live          →  Reddit OAuth live  →  fixture_fallback
NewsAPI live        →  Alpha Vantage news  →  fixture_fallback
TimesFM 2.5         →  local deterministic trend extrapolation
Kalshi live         →  fixture_fallback
SEC API             →  fixture_fallback
```

`source_audit` always reflects which path was taken — no silent fallbacks.

### 4.3 Explainability First

Every output includes:
- `reason` — human-readable explanation of the signal decision
- `drivers` — list of specific signal inputs that drove the decision
- `risk_flags` — list of concerns or data quality issues
- `source_audit` — per-source status, provider, record count, and samples

### 4.4 Paper-First Execution

The execution engine defaults to `paper` mode and **requires two explicit environment variables** to enable live trading:
```
EXECUTION_MODE=live
LIVE_TRADING_ENABLED=true
```

Both must be set simultaneously. Any missing guardrail keeps the system in paper mode.

---

## 5. Competitive Edge

### 5.1 Multi-Source Signal Fusion

Most retail trading tools use a single data source (price only, or sentiment only). market-swarm-lab fuses **five independent signals** — price technicals, social sentiment, news narrative, prediction market odds, and SEC risk — into a single normalized context before any decision is made.

### 5.2 Divergence Detection

The divergence engine computes **pairwise disagreement** between TimesFM, Reddit, and Kalshi:

| Signal Pair | What it detects |
|---|---|
| TimesFM vs Reddit | Are retail crowd and model aligned? |
| TimesFM vs Kalshi | Are prediction markets confirming the model? |
| Reddit vs Kalshi | Is crowd sentiment reflected in market pricing? |

High divergence (`divergence_score > 0.5`) flags **reversal candidates**.  
Strong alignment (`alignment_score > 0.7`) confirms **trend continuation**.

This is a structural edge — most systems don't compare signal sources against each other.

### 5.3 Agent Archetype Isolation

Each of the 100 agents receives **only the data relevant to its archetype**:
- Retail agents don't see SEC filings or TimesFM internals
- Contrarian agents don't get raw price series — they get divergence scores
- Institutional agents get prediction market probabilities, not Reddit sentiment

This prevents cross-contamination and produces more realistic simulation behavior.

### 5.4 Source Freshness as a Confidence Input

Signal confidence is **penalized** when sources are running on fallback data:

```python
confidence = (
    timesfm_confidence × 0.35 +
    alignment_score    × 0.25 +
    simulation_confidence × 0.25 +
    source_freshness   × 0.15   # fraction of sources that are "live"
)
```

A system running on stale fixtures will automatically produce lower-confidence signals and smaller position sizes.

### 5.5 Backtesting Built In

Most signal generators are forward-only. market-swarm-lab includes a rolling backtester that replays the strategy on stored OHLCV history, producing:
- Win rate by strategy type (trend / reversal / volatility)
- Sharpe-like metric
- Equity curve CSV
- By-symbol breakdown (SPY vs NVDA)

This allows the system to **self-validate** before live signals are trusted.

---

## 6. Signal Logic

### Strategy Engine Rules (evaluated in order, first match wins)

| Rule | Condition | Signal |
|---|---|---|
| `trend_confirmation_bullish` | TF bullish + alignment ≥ 0.65 + divergence < 0.35 | CALL |
| `trend_confirmation_bearish` | TF bearish + alignment ≥ 0.65 + divergence < 0.35 | PUT |
| `reversal_bullish` | Crowd bearish (bull_ratio < 0.35) + TF bullish + reversal_candidate | CALL |
| `reversal_bearish` | Crowd very bullish (bull_ratio > 0.65) + TF bearish + reversal_candidate | PUT |
| `volatility_play` | Divergence > 0.6 + rolling_vol_5d > 0.25 | HOLD |
| `no_trade` | Default | HOLD |

### Risk Engine Gates

| Gate | Condition | Action |
|---|---|---|
| Confidence gate | confidence < 0.60 | Reject → HOLD |
| OHLCV gate | ohlcv.status == "fallback" | Reject → HOLD |
| Disagreement gate | disagreement_index > 0.70 | Reject unless volatility strategy |
| No-trade gate | signal == HOLD | size = 0%, not executed |

### Position Sizing

| Confidence | Position Size |
|---|---|
| ≥ 0.80 | 75% |
| ≥ 0.70 | 50% |
| ≥ 0.60 | 25% |
| < 0.60 | 0% (HOLD) |

---

## 7. Option Plan by Horizon

| Horizon | DTE | Strike | Holding Period |
|---|---|---|---|
| 1h | Same-day (0) | ATM | Intraday |
| 1d | 2 DTE | ATM | Overnight |
| 3d | 4 DTE | ATM+1% (call) / ATM-1% (put) | Multi-day |
| 5d | 7 DTE | Delta-based | Multi-day |

Minimum confidence threshold before any option trade: **0.60**.  
If `expected_move_pct` is below 0.3%, no trade is opened.

---

## 8. API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/run-demo` | GET | Full pipeline run — all sources, all layers |
| `/signal` | GET | Signal + risk evaluation for ticker/horizon |
| `/trade-plan` | GET | Signal → risk → paper order (end-to-end) |
| `/backtest` | POST | Rolling replay on OHLCV history |
| `/debug/price` | GET | RSI, vols, momentum, VWAP, price trend |
| `/debug/news` | GET | Headlines, sentiment, themes |
| `/debug/reddit` | GET | Posts, comments, bullish/bearish features |
| `/debug/timesfm` | GET | Forecast, direction, confidence |
| `/health` | GET | Health check |
| `/v1/tickers/{ticker}/run` | POST | Programmatic run with persistence |

---

## 9. Roadmap

| Phase | Feature | Status |
|---|---|---|
| ✅ Phase 1 | Multi-source data collection + normalization | Complete |
| ✅ Phase 2 | TimesFM forecasting + divergence engine | Complete |
| ✅ Phase 3 | 100-agent simulation + seed builder | Complete |
| ✅ Phase 4 | Strategy engine + risk engine | Complete |
| ✅ Phase 5 | Backtester (Phase 1 approximation) | Complete |
| ✅ Phase 6 | Paper execution + portfolio tracking | Complete |
| 🔜 Phase 7 | Real options chain integration (Tradier / Polygon) | Planned |
| 🔜 Phase 8 | Intraday OHLCV (1h bars) | Planned |
| 🔜 Phase 9 | Live execution with broker API | Planned (gated) |
| 🔜 Phase 10 | Web dashboard (React / Next.js) | Planned |

---

## 10. Security & Risk Disclaimers

- **This system is for research and paper trading only.**
- Live trading requires explicit opt-in via environment variables.
- No financial advice is implied by any signal generated.
- Past backtest performance does not guarantee future results.
- All API keys are stored in `.env` (gitignored) and never committed to the repository.
