# market-swarm-lab

A local-first multi-agent market intelligence monorepo. Collects live data from Reddit (via Apify), financial news, SEC filings, prediction markets (Kalshi/Polymarket), and OHLCV feeds — then runs a swarm of AI agents to simulate bullish/bearish/neutral retail positions and generate a structured market report.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    apps/api (FastAPI)                   │
│   /run-demo   /debug/reddit   /report   /agents         │
└──────────────┬──────────────────────────────────────────┘
               │
       ┌───────▼────────┐
       │  MultiSource   │   services/collector
       │  Collector     │   ├── ohlcv.py       (Alpha Vantage)
       └───────┬────────┘   ├── news.py        (NewsAPI)
               │            ├── sec.py         (SEC API / EDGAR)
               │            ├── kalshi.py      (Kalshi)
               │            └── polymarket.py  (Polymarket)
               │
       ┌───────▼────────┐
       │ Reddit         │   services/reddit-collector
       │ Collector      │   ├── apify_reddit_fetcher.py   ← Apify MCP
       └───────┬────────┘   ├── apify_normalizer.py
               │            ├── reddit_collector_service.py
               │            └── nlp.py
               │
       ┌───────▼────────┐
       │  Seed Builder  │   services/seed-builder
       │                │   └── seed_builder_service.py
       └───────┬────────┘        build_reddit_context()
               │                 → retail_sentiment_summary
               │                 → key_bullish/bearish_points
               │                 → most_upvoted_arguments
               │                 → reddit_confidence
               │
       ┌───────▼────────┐
       │  Agent Seeder  │   services/agent-seeder
       │                │   └── agent_seeder_service.py
       └───────┬────────┘        retail agents get full reddit_context
               │
       ┌───────▼────────┐
       │  Simulation    │   services/forecasting (TimesFM 2.5)
       │  Engine        │   services/reporting
       └────────────────┘
```

### Reddit Ingestion Priority

1. **Apify live** — `APIFY_API_TOKEN` set → runs `trudax/reddit-scraper` for r/wallstreetbets, r/stocks, r/options
2. **OAuth live** — `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET` set
3. **Fixture fallback** — loads from `infra/fixtures/reddit/`

`source_audit.reddit` will report: `status`, `provider`, `record_count`, `sample_post_titles`

---

## Prerequisites

- Python 3.11+
- Docker & Docker Compose
- API keys (see Environment below)

---

## Setup

```bash
# 1. Clone
git clone https://github.com/lakshmanb4u/TradingBotMiroFish.git
cd TradingBotMiroFish

# 2. Copy env template and fill in your keys
cp .env.example .env
# Edit .env with your API keys

# 3. Install Python deps (local dev)
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. Or run via Docker
make setup
make run
```

---

## Required Environment Variables

| Variable | Description | Required |
|---|---|---|
| `APIFY_API_TOKEN` | Apify API token for Reddit scraping | ✅ (recommended) |
| `APIFY_REDDIT_ACTOR` | Apify actor ID (default: `trudax/reddit-scraper`) | optional |
| `ALPHAVANTAGE_API_KEY` | Alpha Vantage for OHLCV data | ✅ |
| `NEWSAPI_API_KEY` | NewsAPI for financial news | ✅ |
| `SEC_API_KEY` | SEC API for filings | optional |
| `REDDIT_CLIENT_ID` | Reddit OAuth (fallback if no Apify) | optional |
| `REDDIT_CLIENT_SECRET` | Reddit OAuth secret | optional |
| `POSTGRES_DSN` | PostgreSQL connection string | optional |
| `REDIS_URL` | Redis URL for report caching | optional |
| `MIROFISH_BASE_URL` | MiroFish bridge URL | optional |
| `ENABLE_TIMESFM` | Enable TimesFM 2.5 forecasting (`true`/`false`) | optional |

See `.env.example` for full list with defaults.

---

## How to Run Demo

```bash
# Docker
make demo

# Or directly via API
curl "http://localhost:8000/run-demo?ticker=SPY"
curl "http://localhost:8000/run-demo?ticker=NVDA"
```

### Debug Endpoints

```bash
# Reddit collection status + sample data
curl "http://localhost:8000/debug/reddit?ticker=SPY"

# Full run report
curl "http://localhost:8000/report/SPY"
```

---

## Services

| Service | Port | Description |
|---|---|---|
| API | 8000 | FastAPI — orchestration + reports |
| Postgres | 5432 | Run report persistence |
| Redis | 6379 | Report cache |

---

## Project Structure

```
market-swarm-lab/
├── apps/api/               # FastAPI application
├── services/
│   ├── collector/          # OHLCV, news, SEC, Kalshi, Polymarket
│   ├── reddit-collector/   # Apify + OAuth Reddit ingestion
│   ├── seed-builder/       # Builds simulation seed from normalized data
│   ├── agent-seeder/       # Seeds AI agents with market context
│   ├── forecasting/        # TimesFM 2.5 price forecasting
│   ├── reporting/          # Markdown + JSON report generation
│   └── mirofish-bridge/    # MiroFish integration
├── infra/
│   └── fixtures/           # Fallback data for offline dev
├── state/                  # Runtime artifacts (gitignored)
├── docs/
└── .env.example
```

---

## Security

- **Never commit `.env`** — it's in `.gitignore`
- `state/raw/` and `state/seeds/` are gitignored (may contain API responses)
- Only `.env.example` with placeholder values is tracked
