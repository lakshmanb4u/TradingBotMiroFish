"""
Agent Seeder FastAPI service.

Endpoints:
  GET  /health
  POST /agents/seed
  POST /agents/simulate
"""
from __future__ import annotations

import sys
import os

# Ensure this directory is on sys.path for direct imports (directory name has a hyphen)
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import Any

from agent_seeder_service import AgentSeederService  # noqa: E402

app = FastAPI(title="agent-seeder", version="0.1.0")

_service = AgentSeederService()


class SeedRequest(BaseModel):
    seed: dict = Field(default_factory=dict)
    forecast: dict = Field(default_factory=dict)
    normalized_bundle: dict = Field(default_factory=dict)


class SimulateRequest(BaseModel):
    agent_roster: dict = Field(default_factory=dict)
    horizon_days: int = Field(default=5)


class TradeSignalRequest(BaseModel):
    simulation_result: dict = Field(default_factory=dict)
    forecast: dict = Field(default_factory=dict)
    normalized: dict = Field(default_factory=dict)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "agent-seeder"}


@app.post("/agents/seed")
def seed_agents(request: SeedRequest) -> dict:
    return _service.seed_agents(
        seed=request.seed,
        forecast=request.forecast,
        normalized_bundle=request.normalized_bundle,
    )


@app.post("/agents/simulate")
def simulate(request: SimulateRequest) -> dict:
    return _service.run_simulation(
        agent_roster=request.agent_roster,
        horizon_days=request.horizon_days,
    )


@app.post("/agents/trade-signal")
def trade_signal(request: TradeSignalRequest) -> dict:
    return _service.generate_trade_signal(
        simulation_result=request.simulation_result,
        forecast=request.forecast,
        normalized=request.normalized,
    )
