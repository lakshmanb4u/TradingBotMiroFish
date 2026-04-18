"""
MiroFish bridge FastAPI app.

POST /simulate
  Input:
    documents[]       - list of {source, content} or plain strings
    forecast_summary  - {direction, confidence, forecast_close_5d, delta_5d}
    personas_config   - list of agent personas
    scenario          - natural language simulation prompt

  Output:
    distribution      - {bullish, bearish, neutral} float shares
    agent_reasoning_summary - human-readable round-by-round summary
    final_direction   - "bullish" | "bearish" | "rangebound"
    provider_mode     - which mode ran
    (+ full round/persona detail)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from mirofish_bridge_service import MiroFishBridgeService  # noqa: E402

app = FastAPI(title="market-swarm-lab-mirofish-bridge", version="0.1.0")
service = MiroFishBridgeService()


class Document(BaseModel):
    source: str = "uploaded"
    content: str


class SimulateRequest(BaseModel):
    documents: list[Document | str] = Field(default_factory=list)
    forecast_summary: dict[str, Any] = Field(default_factory=dict)
    personas_config: list[dict[str, Any]] = Field(default_factory=list)
    scenario: str = ""


class Distribution(BaseModel):
    bullish: float
    bearish: float
    neutral: float


class SimulateResponse(BaseModel):
    distribution: Distribution
    agent_reasoning_summary: str
    final_direction: str
    provider_mode: str
    outlook_score: float | None = None
    rounds: list[dict[str, Any]] = Field(default_factory=list)
    persona_reasoning: list[dict[str, Any]] = Field(default_factory=list)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "mirofish-bridge"}


@app.post("/simulate", response_model=SimulateResponse)
def simulate(request: SimulateRequest) -> dict:
    docs = [
        (
            {"source": d.source, "content": d.content}
            if isinstance(d, Document)
            else {"source": "uploaded", "content": d}
        )
        for d in request.documents
    ]
    payload = {
        "documents": docs,
        "forecast_summary": request.forecast_summary,
        "personas_config": request.personas_config,
        "scenario": request.scenario,
    }
    result = service.simulate(payload)
    dist = result.get("distribution", {})
    result["distribution"] = {
        "bullish": dist.get("bullish", 0.0),
        "bearish": dist.get("bearish", 0.0),
        "neutral": dist.get("neutral", 0.0),
    }
    return result
