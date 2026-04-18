# market-swarm-lab

A local-first prototype that combines:
- MiroFish-style agent simulation via a bridge service
- TimesFM-compatible forecasting with a safe local fallback
- Multi-source ingestion for SEC filings, news, Reddit, prediction markets, and market data
- A unified FastAPI endpoint to run a report for a ticker

## Quick start

One-command demo for NVDA and SPY:

```bash
make demo
```

That command runs the API container with Postgres + Redis dependencies, executes the demo workflow for `NVDA` and `SPY`, and writes:
- `state/reports/NVDA.json`
- `state/reports/NVDA.md`
- `state/reports/SPY.json`
- `state/reports/SPY.md`

## API

Start the stack:

```bash
make up
```

Run a ticker workflow:

```bash
curl -X POST http://localhost:8000/v1/tickers/NVDA/run
```

## Reddit behavior

Reddit is treated as a first-class source in two ways:
- Simulation seed, retail sentiment narratives, and agent personas for the MiroFish bridge
- Derived numeric features for the forecasting input window

If Reddit OAuth credentials are not available, the system automatically falls back to bundled fixtures and continues.
