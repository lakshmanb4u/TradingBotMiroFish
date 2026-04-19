# market-swarm-lab

> A local-first multi-agent market intelligence system. Collects live data from five sources, runs a swarm of 100 AI agents across four archetypes, detects signal divergence, and generates a structured trade report with a final BUY/SELL/HOLD signal.

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

---

## What It Does

1. **Collects** live OHLCV prices, Reddit sentiment, financial news, SEC filings, and prediction market odds
2. **Forecasts** with TimesFM 2.5 (or a deterministic local fallback) using 60–100 days of real price data
3. **Detects divergence** across TimesFM vs Reddit vs Kalshi signals
4. **Seeds 100 agents** — retail, institutional, momentum, contrarian — each with source-specific context
5. **Simulates** a market vote and generates a trade signal
6. **Reports** structured JSON + Markdown output with full source audit

---

## Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full technical deep-dive.

### Quick Overview

```
Alpha Vantage ──► price-collector  ─────────────────────────────┐
Apify/Reddit  ──► reddit-collector ─────────────────────────────►  normalized_bundle
NewsAPI       ──► news-collector   ─────────────────────────────►       │
SEC/EDGAR     ──► collector        ─────────────────────────────►       │
Kalshi        ──► collector        ─────────────────────────────┘       │
                                                                         ▼
                                               TimesFM forecast ──► Divergence Engine
                                                                         │
                                                                    Seed Builder
                                                                    (seed_pack)
                                                                         │
                                                              100 Agents (retail/inst/
                                                              momentum/contrarian)
                                                                         │
                                                              Simulation + Report
                                                           (BUY / SELL / HOLD signal)
```

### Agent Archetypes

| Archetype | Count | Data Sources |
|---|---|---|
| Retail | 40 | Reddit sentiment + News |
| Institutional | 30 | SEC filings + TimesFM + Kalshi |
| Momentum | 20 | OHLCV (RSI, vol, VWAP) + TimesFM |
| Contrarian | 10 | Divergence scores (TimesFM vs Reddit vs Kalshi) |

---

## Project Structure

```
market-swarm-lab/
├── apps/
│   └── api/                        # FastAPI app — orchestration + endpoints
│       ├── main.py                 # Routes: /run-demo, /debug/*, /health
│       ├── workflow.py             # Full pipeline workflow
│       └── db.py                  # PostgreSQL + Redis helpers
│
├── services/
│   ├── price-collector/            # Alpha Vantage OHLCV + technical indicators
│   │   ├── alpha_vantage_client.py # Low-level AV REST client
│   │   ├── price_service.py        # RSI-14, vol, momentum, VWAP, Parquet output
│   │   └── price_collector_service.py
│   │
│   ├── reddit-collector/           # Apify → OAuth → fixture priority chain
│   │   ├── apify_reddit_fetcher.py # Runs trudax/reddit-scraper via Apify API
│   │   ├── apify_normalizer.py     # Normalizes Apify output
│   │   ├── reddit_collector_service.py
│   │   └── nlp.py                  # Sentiment scoring + feature extraction
│   │
│   ├── news-collector/             # NewsAPI → AV news → fixture
│   │   ├── newsapi_client.py       # Low-level NewsAPI client
│   │   ├── news_service.py         # Full pipeline with narrative_strength, breaking_news
│   │   └── news_collector_service.py
│   │
│   ├── collector/                  # Multi-source collector (SEC, Kalshi, Polymarket)
│   │   └── fetchers/               # ohlcv.py, news.py, sec.py, kalshi.py, polymarket.py
│   │
│   ├── normalizer/                 # Unified normalization into normalized_bundle
│   ├── forecasting/                # TimesFM 2.5 + local fallback
│   │   └── forecasting_service.py  # forecast_from_prices() + direction/confidence
│   │
│   ├── seed-builder/               # Simulation seed construction
│   │   ├── seed_builder_service.py # build_seed_pack() unified narrative
│   │   └── divergence_engine.py    # Cross-signal divergence detection
│   │
│   ├── agent-seeder/               # 100-agent seeding + simulation
│   │   ├── agent_seeder_service.py
│   │   └── prompt_generator.py
│   │
│   ├── reporting/                  # JSON + Markdown report generation
│   └── mirofish-bridge/            # Optional MiroFish simulation bridge
│
├── infra/
│   └── fixtures/                   # Offline fallback data
│       ├── market_data/
│       ├── reddit/
│       ├── news/
│       ├── sec/
│       └── prediction_markets/
│
├── state/                          # Runtime artifacts (gitignored)
│   ├── raw/                        # Raw API responses
│   ├── seeds/                      # Simulation seeds
│   ├── cache/
│   └── reports/                    # Final JSON + Markdown reports
│
├── data/                           # Normalized data store (gitignored)
│   └── market_data/
│       ├── ohlcv/                  # Parquet files
│       └── news/                   # Normalized JSON
│
├── docs/
│   └── ARCHITECTURE.md             # Full technical architecture
│
├── pyproject.toml
├── .env.example
└── docker-compose.yml
```

---

## Setup

### Prerequisites

- Python 3.11+
- Docker & Docker Compose (for containerized run)

### Local Dev

```bash
# 1. Clone
git clone https://github.com/lakshmanb4u/TradingBotMiroFish.git
cd TradingBotMiroFish

# 2. Copy env and fill in API keys
cp .env.example .env
# Edit .env — minimum required: ALPHAVANTAGE_API_KEY, NEWSAPI_API_KEY, APIFY_API_TOKEN

# 3. Create virtualenv and install deps
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]" 2>/dev/null || pip install -r requirements.txt 2>/dev/null || \
  pip install fastapi uvicorn httpx pandas pyarrow python-dotenv pydantic

# 4. Run the API
uvicorn apps.api.main:app --reload --port 8000
```

### Docker

```bash
make setup   # creates .env from .env.example + builds containers
make run     # starts all services
make demo    # runs demo for NVDA + SPY
```

---

## Required Environment Variables

| Variable | Description | Required |
|---|---|---|
| `ALPHAVANTAGE_API_KEY` | Alpha Vantage — OHLCV + news | ✅ |
| `NEWSAPI_API_KEY` | NewsAPI — financial headlines | ✅ |
| `APIFY_API_TOKEN` | Apify — Reddit scraping via `trudax/reddit-scraper` | ✅ recommended |
| `APIFY_REDDIT_ACTOR` | Actor ID (default: `trudax/reddit-scraper`) | optional |
| `SEC_API_KEY` | SEC API — EDGAR filings | optional |
| `REDDIT_CLIENT_ID` | Reddit OAuth — fallback if no Apify | optional |
| `REDDIT_CLIENT_SECRET` | Reddit OAuth secret | optional |
| `ENABLE_TIMESFM` | `true` to load TimesFM 2.5 200M model | optional |
| `POSTGRES_DSN` | PostgreSQL for run report persistence | optional |
| `REDIS_URL` | Redis for report caching | optional |
| `MIROFISH_BASE_URL` | MiroFish simulation engine URL | optional |

See `.env.example` for all defaults.

---

## API Usage

### Run Full Pipeline

```bash
curl "http://localhost:8000/run-demo?ticker=SPY"
curl "http://localhost:8000/run-demo?ticker=NVDA"
```

**Response includes:**
- `source_audit` — all 5 sources (status/provider/record_count)
- `seed_pack` — unified price + TimesFM + news + Reddit + Kalshi summaries
- `divergence` — divergence_score, alignment_score, signal
- `simulation` — agent vote breakdown + final_direction
- `trade_signal` — BUY/SELL/HOLD with confidence
- `report` — full Markdown + JSON report

### Debug Endpoints

```bash
# OHLCV: RSI, volatility, momentum, VWAP, price trend
curl "http://localhost:8000/debug/price?ticker=SPY"

# News: headlines, sentiment, bullish/bearish themes
curl "http://localhost:8000/debug/news?ticker=SPY"

# Reddit: posts, comments, features (bullish_ratio, disagreement_index)
curl "http://localhost:8000/debug/reddit?ticker=SPY"

# TimesFM: forecast, direction, confidence, trend_strength
curl "http://localhost:8000/debug/timesfm?ticker=SPY"
```

### Example: source_audit Response

```json
{
  "source_audit": {
    "ohlcv":   {"status": "live",     "provider": "alphavantage", "record_count": 100},
    "reddit":  {"status": "live",     "provider": "apify",        "record_count": 60,  "sample_post_titles": ["..."]},
    "news":    {"status": "live",     "provider": "newsapi",      "record_count": 20,  "sample_headlines": ["..."]},
    "kalshi":  {"status": "live",     "provider": "kalshi",       "record_count": 3},
    "sec":     {"status": "fallback", "provider": "sec_api",      "record_count": 0}
  }
}
```

### Example: divergence Response

```json
{
  "divergence": {
    "timesfm_score": 1.0,
    "reddit_score": 0.28,
    "kalshi_score": 0.6,
    "timesfm_vs_reddit": 0.36,
    "timesfm_vs_kalshi": 0.20,
    "reddit_vs_kalshi": 0.16,
    "divergence_score": 0.24,
    "alignment_score": 0.76,
    "signal": "trend_confirmation"
  }
}
```

---

## Key Design Decisions

### Fallback Priority

Every data source has a graceful degradation chain — the pipeline never crashes on missing API keys:

```
Alpha Vantage live → fixture_fallback
Apify live → Reddit OAuth live → fixture_fallback
NewsAPI live → Alpha Vantage news live → fixture_fallback
TimesFM 2.5 → local deterministic fallback
```

### No Silent Fallbacks

`source_audit` is always included in `/run-demo` responses. Every source explicitly reports its status (`live` or `fallback`) so you know exactly what data powered each run.

### Agent Isolation

Each of the 100 agents only receives data relevant to its archetype. Retail agents don't see SEC filings; contrarian agents don't get raw price series — they get divergence scores.

---

## TimesFM Notes

TimesFM 2.5 (200M parameter PyTorch model) is **optional** and disabled by default.

To enable:
```bash
ENABLE_TIMESFM=true pip install timesfm torch
```

Without it, the forecasting service uses a deterministic trend extrapolation fallback that produces the same output schema.

---

## Contributing

1. Fork the repo
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Commit: `git commit -m "feat: description"`
4. Push and open a PR

**Never commit `.env` or any file under `state/` or `data/`.**

---

## License

MIT
