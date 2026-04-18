# MiroFish integration

The MiroFish source lives at `external/mirofish/` and is not modified.
The bridge service lives at `services/mirofish-bridge/`.

## Three operating modes

### 1. remote_live

Set `MIROFISH_BASE_URL=http://localhost:5001` (or wherever MiroFish is running).

When the bridge starts it health-checks MiroFish.
If it responds, it builds a seed packet from your documents, personas, and forecast,
POSTs it to `MiroFish /api/graph/ontology/generate`, and translates the response
into the bridge output schema.

### 2. file_adapter (recommended for first-time use)

Set `MIROFISH_FILE_ADAPTER_DIR=state/mirofish_seeds` (or leave unset — the bridge defaults to that path).

The bridge writes a `<seed_id>_seed.json` file for every `/simulate` call
and returns a `provider_mode: mirofish_file_adapter_pending` response
alongside a local fallback result so the pipeline keeps running.

Manual run flow:

Step 1 — start MiroFish:
```bash
cd external/mirofish
cp .env.example .env
# fill in LLM_API_KEY and ZEP_API_KEY in .env
npm run setup:all
npm run dev
```

Step 2 — open the MiroFish UI at http://localhost:3000

Step 3 — upload content from the seed file:
The `documents` array in `state/mirofish_seeds/<seed_id>_seed.json`
contains pre-extracted text from Reddit, news, and SEC sources.
Copy the content of each document entry and paste/upload via the UI.
Use the `simulation_requirement` field as the prediction prompt.

Step 4 — run the simulation and wait for the report.

Step 5 — write the result to `state/mirofish_seeds/<seed_id>_result.json`:
```json
{
  "distribution": {
    "bullish": 0.64,
    "bearish": 0.22,
    "neutral": 0.14
  },
  "agent_reasoning_summary": "Paste summary from MiroFish report here.",
  "final_direction": "bullish"
}
```

The next call to `/simulate` with the same seed_id will load this result
and return `provider_mode: mirofish_file_adapter_result_loaded`.

### 3. local_fallback

No env vars set. A deterministic in-process simulation runs using
Reddit sentiment, prediction market consensus, forecast direction, and confidence.
Always available. Never blocks.

## Bridge API

```
POST http://localhost:8004/simulate
Content-Type: application/json

{
  "documents": [
    {"source": "reddit", "content": "NVDA still has room if hyperscalers keep spending"},
    {"source": "news",   "content": "NVIDIA supply chain tightens..."}
  ],
  "forecast_summary": {
    "direction": "up",
    "confidence": 0.58,
    "forecast_close_5d": 1001.91,
    "delta_5d": 35.21
  },
  "personas_config": [
    {"name": "Retail Momentum Trader", "stance": "bullish", "weight": 0.75},
    {"name": "Sell-Side Analyst",      "stance": "constructive", "weight": 0.6},
    {"name": "Compliance Watcher",     "stance": "monitoring",   "weight": 0.35}
  ],
  "scenario": "Simulate agent reactions for NVDA during an AI capex acceleration cycle."
}
```

Response:
```json
{
  "distribution": {
    "bullish": 0.663,
    "bearish": 0.337,
    "neutral": 0.0
  },
  "agent_reasoning_summary": "3 agents simulated over 3 rounds...",
  "final_direction": "bullish",
  "provider_mode": "local_mirofish_fallback",
  "outlook_score": 32.6,
  "rounds": [...],
  "persona_reasoning": [...]
}
```

## Environment variables

| Variable | Purpose |
|---|---|
| `MIROFISH_BASE_URL` | HTTP base URL of a running MiroFish backend |
| `MIROFISH_FILE_ADAPTER_DIR` | Directory for seed/result file exchange |
