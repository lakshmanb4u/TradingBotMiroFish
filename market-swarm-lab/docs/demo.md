# Demo

## One-command run

```bash
make demo
```

This runs:

```bash
docker compose run --rm api python -m apps.api.demo NVDA SPY
```

## Outputs

Reports land in `state/reports/` as both JSON and Markdown.

Examples:
- `state/reports/NVDA_<timestamp>.json`
- `state/reports/NVDA_<timestamp>.md`
- `state/reports/SPY_<timestamp>.json`
- `state/reports/SPY_<timestamp>.md`

## API example

```bash
curl -X POST http://localhost:8000/v1/tickers/NVDA/run
```

## Local non-Docker smoke test

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip setuptools wheel
pip install fastapi uvicorn pydantic httpx python-dotenv 'psycopg[binary]' redis
PYTHONPATH=. python -m apps.api.demo NVDA SPY
```
