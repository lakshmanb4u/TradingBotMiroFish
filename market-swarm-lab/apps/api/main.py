from __future__ import annotations

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

from .db import init_infra
from .workflow import run_ticker_workflow


class RunResponse(BaseModel):
    ticker: str
    provider_modes: dict
    raw: dict
    normalized: dict
    forecast: dict
    simulation: dict
    report: dict


class RunRequest(BaseModel):
    persist: bool = Field(default=True)


app = FastAPI(title="market-swarm-lab", version="0.1.0")


@app.on_event("startup")
def startup() -> None:
    init_infra()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "market-swarm-lab"}


@app.post("/v1/tickers/{ticker}/run", response_model=RunResponse)
def run_ticker(ticker: str, request: RunRequest | None = None) -> dict:
    persist = True if request is None else request.persist
    return run_ticker_workflow(ticker=ticker, persist=persist)


@app.get("/run-demo")
def run_demo(ticker: str = Query(default="NVDA")) -> dict:
    import sys
    import os
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[2]
    SERVICE_DIRS = [
        ROOT / "services" / "collector",
        ROOT / "services" / "reddit-collector",
        ROOT / "services" / "normalizer",
        ROOT / "services" / "forecasting",
        ROOT / "services" / "mirofish-bridge",
        ROOT / "services" / "reporting",
        ROOT / "services" / "seed-builder",
        ROOT / "services" / "agent-seeder",
    ]
    for sd in SERVICE_DIRS:
        sp = str(sd)
        if sp not in sys.path:
            sys.path.insert(0, sp)

    from collector_service import MultiSourceCollector
    from normalizer_service import UnifiedNormalizerService
    from forecasting_service import TimesFMForecastingService
    from reddit_collector_service import RedditCollectorService
    from seed_builder_service import SeedBuilderService
    from agent_seeder_service import AgentSeederService
    from prompt_generator import SimulationPromptGenerator
    from mirofish_bridge_service import MiroFishBridgeService
    from unified_reporter import UnifiedReporter

    # 1. Collect
    raw_bundle = MultiSourceCollector().collect(ticker)

    # 2. Normalize
    normalized_bundle = UnifiedNormalizerService().normalize(ticker, raw_bundle)

    # 3. Forecast
    forecast = TimesFMForecastingService().forecast(ticker, normalized_bundle)

    # 4. Reddit seed data
    reddit_seed = RedditCollectorService().extract_seed_data(ticker)

    # 5. Build seed
    seed = SeedBuilderService().build(ticker, normalized_bundle, forecast)

    # 6. Seed agents
    seeder = AgentSeederService()
    agent_roster_result = seeder.seed_agents(seed, forecast, normalized_bundle)

    # 7. Run simulation
    simulation_result = seeder.run_simulation(agent_roster_result)

    # 8. Generate prompt
    prompt_result = SimulationPromptGenerator().generate(ticker, seed, agent_roster_result)

    # 9. MiroFish simulate
    mirofish_result = MiroFishBridgeService().simulate(prompt_result["mirofish_seed_packet"])

    # Merge simulation results into mirofish_result for unified reporter
    if "final_direction" not in mirofish_result:
        mirofish_result["final_direction"] = simulation_result.get("final_direction", "rangebound")
    if "outlook_score" not in mirofish_result:
        mirofish_result["outlook_score"] = simulation_result.get("buy_sell_ratio", 1.0)

    # 10. Unified report
    report = UnifiedReporter().report(
        ticker, forecast, reddit_seed, normalized_bundle, mirofish_result, seed
    )

    return {
        "ticker": ticker.upper(),
        "raw_bundle_keys": list(raw_bundle.keys()),
        "forecast": forecast,
        "reddit_seed": reddit_seed,
        "seed": seed,
        "agent_roster_summary": {
            "total_agents": len(agent_roster_result.get("agent_roster", [])),
            "archetypes": {k: v["count"] for k, v in agent_roster_result.get("archetypes", {}).items()},
        },
        "simulation": simulation_result,
        "prompt": {
            "simulation_prompt": prompt_result["simulation_prompt"],
            "agent_summary": prompt_result["agent_summary"],
        },
        "mirofish": mirofish_result,
        "report": report,
    }
