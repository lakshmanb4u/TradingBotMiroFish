# Local Setup Guide

> Run MiroFish / market-swarm-lab on your own machine — no cloud required.

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11 or 3.12 | 3.13+ not supported |
| Docker + Docker Compose | any recent | for the full multi-service stack |
| Git | any | to clone the repo |

---

## API Keys

You need at least two keys to get live data. Everything else falls back to bundled fixture data.

| Key | Where to get it | Required? |
|---|---|---|
| `ALPHAVANTAGE_API_KEY` | https://www.alphavantage.co/support/#api-key (free tier works) | ✅ Yes |
| `NEWSAPI_API_KEY` | https://newsapi.org/register (free tier works) | ✅ Yes |
| `APIFY_API_TOKEN` | https://console.apify.com/account/integrations | Recommended (Reddit live data) |
| `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` | https://www.reddit.com/prefs/apps → "script" app | Optional (fallback if no Apify) |
| `SEC_API_KEY` | https://sec-api.io | Optional |
| `ENABLE_TIMESFM` | Set to `true` in `.env` | Optional (see TimesFM section) |

> **No keys at all?** The service still runs using bundled fixture data for all sources. Set `ENABLE_TIMESFM=false` and leave all API keys blank — you'll get deterministic fallback forecasts.

---

## Option A — Docker (recommended, full stack)

This spins up the API, Postgres, Redis, collector, forecasting, reporting, seed-builder, agent-seeder, and mirofish-bridge in one command.

```bash
# 1. Clone
git clone https://github.com/lakshmanb4u/TradingBotMiroFish.git
cd TradingBotMiroFish/market-swarm-lab

# 2. Set up environment
cp .env.example .env
# Open .env and fill in at minimum:
#   ALPHAVANTAGE_API_KEY=your_key
#   NEWSAPI_API_KEY=your_key
# (all other keys are optional)

# 3. Build and start
docker compose up --build

# 4. Verify it's running
curl http://localhost:8000/health
```

The API is now live at **http://localhost:8000**.

### Service ports

| Service | Port | Purpose |
|---|---|---|
| api | 8000 | Main orchestration + /run-demo |
| collector | 8001 | Multi-source data collection |
| forecasting | 8002 | TimesFM / fallback forecasts |
| reporting | 8003 | JSON + Markdown report output |
| mirofish-bridge | 8004 | MiroFish simulation bridge |
| seed-builder | 8005 | Agent seed pack construction |
| agent-seeder | 8006 | 100-agent seeding + simulation |
| postgres | 5432 | Run report persistence |
| redis | 6379 | Report caching |

---

## Option B — Local Python (no Docker, API only)

If you just want to run the FastAPI app without Docker:

```bash
# 1. Clone
git clone https://github.com/lakshmanb4u/TradingBotMiroFish.git
cd TradingBotMiroFish/market-swarm-lab

# 2. Create and activate a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -e .

# 4. Set up environment
cp .env.example .env
# Edit .env with your API keys (or leave blank for fixture-only mode)

# 5. Start the API
uvicorn apps.api.main:app --reload --port 8000
```

> **Note:** Without Docker, Postgres and Redis are not running. The API gracefully degrades — run reports won't be persisted, but the pipeline still works in-memory.

---

## Running a Forecast

Once the API is up:

```bash
# Full pipeline for SPY (returns BUY/SELL/HOLD signal + full audit)
curl "http://localhost:8000/run-demo?ticker=SPY"

# Full pipeline for NVDA
curl "http://localhost:8000/run-demo?ticker=NVDA"
```

### Debug endpoints (individual data sources)

```bash
# OHLCV: RSI, volatility, momentum, VWAP
curl "http://localhost:8000/debug/price?ticker=SPY"

# News: headlines, sentiment, bullish/bearish themes
curl "http://localhost:8000/debug/news?ticker=SPY"

# Reddit: posts, comments, bullish_ratio, disagreement_index
curl "http://localhost:8000/debug/reddit?ticker=SPY"

# TimesFM: forecast direction, confidence, trend strength
curl "http://localhost:8000/debug/timesfm?ticker=SPY"

# Health check
curl "http://localhost:8000/health"
```

---

## Enabling TimesFM 2.5

TimesFM is disabled by default (it's a 200M parameter PyTorch model — large download).

To enable it:

```bash
# In .env
ENABLE_TIMESFM=true

# Install the extra deps
pip install timesfm torch

# Or for Docker, set the build arg:
INSTALL_TIMESFM=true docker compose up --build
```

Without TimesFM, the forecasting service uses a deterministic trend extrapolation fallback. The output schema is identical — you won't see errors, just slightly different confidence values.

---

## Makefile Shortcuts

```bash
make setup   # cp .env.example .env + docker compose build
make run     # docker compose up
make demo    # run /run-demo for NVDA + SPY
make stop    # docker compose down
```

---

## Troubleshooting

**`ModuleNotFoundError` on startup**
→ Make sure you're in the `market-swarm-lab/` directory, not the repo root, and that your venv is activated.

**All sources showing `fallback` in source_audit**
→ Normal if no API keys are set. Check your `.env` has the keys without quotes or trailing spaces.

**Port already in use**
→ Change the host port in `docker-compose.yml`, e.g. `"8080:8000"` for the api service.

**TimesFM OOM / slow**
→ It loads a 200M param model. Needs ~4GB RAM. Disable with `ENABLE_TIMESFM=false` if your machine is constrained.

**Postgres connection refused (local Python mode)**
→ Expected — the API degrades gracefully without Postgres. Run Docker Compose if you need persistence.

---

## What the Response Looks Like

A `/run-demo?ticker=SPY` response includes:

- `source_audit` — which sources returned live data vs fixture fallback
- `seed_pack` — unified price + TimesFM + news + Reddit + Kalshi summaries
- `divergence` — divergence_score, alignment_score, cross-signal conflicts
- `simulation` — 100-agent vote breakdown by archetype
- `trade_signal` — final BUY / SELL / HOLD with confidence score
- `report` — full Markdown + JSON report

---

## Project Layout (quick reference)

```
market-swarm-lab/
├── apps/api/          # FastAPI — /run-demo, /debug/*, /health
├── services/          # Individual microservices (collector, forecasting, etc.)
├── infra/fixtures/    # Offline fallback data (no API key needed)
├── state/             # Runtime artifacts — gitignored
├── docs/              # Architecture + technical design docs
├── .env.example       # Environment variable template
└── docker-compose.yml # Full stack definition
```

Full architecture: [`docs/ARCHITECTURE.md`](ARCHITECTURE.md)
