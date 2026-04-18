# Architecture

## Monorepo layout

- `services/collector`
- `services/reddit-collector`
- `services/normalizer`
- `services/forecasting`
- `services/mirofish-bridge`
- `services/reporting`
- `apps/api`
- `infra`
- `docs`

## Flow

1. `collector` gathers SEC, news, prediction markets, market data, and Reddit.
2. `reddit-collector` prefers official OAuth and falls back to fixtures when missing or unavailable.
3. `normalizer` produces two outputs from the same raw Reddit payload:
   - simulation seed, narratives, and personas for MiroFish
   - numeric feature window for the forecast model
4. `forecasting` uses a TimesFM-compatible adapter, with a local fallback when TimesFM is unavailable.
5. `mirofish-bridge` prepares a seed packet and runs a local-first fallback simulation when remote MiroFish is not reachable.
6. `reporting` emits JSON and Markdown artifacts.
7. `apps/api` exposes one unified ticker workflow endpoint.

## Reddit as a first-class source

Reddit is used twice by design:
- qualitative: thread titles, bodies, and comment tone shape retail narratives and agent personas
- quantitative: mention count, comment count, bullish ratio, bearish ratio, and average sentiment are emitted as time-aligned numeric features for forecasting

## Reliability rules

- Missing Reddit credentials must not break the run.
- Missing remote MiroFish must not break the run.
- Missing TimesFM install must not break the run.
- Fixture mode is always available for NVDA and SPY.
