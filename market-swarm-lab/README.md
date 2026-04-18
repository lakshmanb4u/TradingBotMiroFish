# market-swarm-lab

Simple local-first monorepo for market simulations and forecasts.

## Structure

- `services/collector`
- `services/reddit-collector`
- `services/normalizer`
- `services/forecasting`
- `services/mirofish-bridge`
- `services/reporting`
- `apps/api`
- `infra`
- `docs`

## Prerequisites

- Docker
- Docker Compose

## Setup

```bash
make setup
```

This will:
- create `.env` from `.env.example` if needed
- build the local containers

## Run everything locally

```bash
make run
```

Services:
- API: `http://localhost:8000`
- Collector: `http://localhost:8001`
- Forecasting: `http://localhost:8002`
- Reporting: `http://localhost:8003`
- Postgres: `localhost:5432`
- Redis: `localhost:6379`

## Demo

```bash
make demo
```

That runs the demo workflow for `NVDA` and `SPY` and writes JSON + Markdown reports to `state/reports/`.

## Environment

`.env.example` includes:
- `NEWSAPI_API_KEY`
- `ALPHAVANTAGE_API_KEY`
- `REDDIT_CLIENT_ID`
- `REDDIT_CLIENT_SECRET`

Reddit is optional. If credentials are missing, the system falls back to local fixtures and keeps running.
