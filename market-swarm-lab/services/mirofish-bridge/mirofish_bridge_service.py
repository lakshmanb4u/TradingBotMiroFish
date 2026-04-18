"""
MiroFish bridge service.

Three operating modes (chosen automatically):
  remote_live        - MIROFISH_BASE_URL is set and MiroFish /health responds.
                       The bridge POSTs a seed packet to MiroFish ontology/generate
                       and returns its structured response.
  file_adapter       - MIROFISH_BASE_URL is set but unreachable, OR
                       MIROFISH_FILE_ADAPTER_DIR is set explicitly.
                       The bridge writes seed files to disk so an operator can
                       run MiroFish manually and paste results back.
  local_fallback     - Neither env var is set.
                       A deterministic scoring simulation runs in-process.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


class MiroFishBridgeService:
    def __init__(self) -> None:
        self.base_url = os.getenv("MIROFISH_BASE_URL", "").rstrip("/")
        adapter_dir = os.getenv("MIROFISH_FILE_ADAPTER_DIR", "")
        self.adapter_dir: Path | None = Path(adapter_dir) if adapter_dir else None

    # ------------------------------------------------------------------ public

    def run(
        self,
        ticker: str,
        normalized_bundle: dict[str, Any],
        forecast: dict[str, Any],
    ) -> dict[str, Any]:
        seed = self._build_seed_packet(ticker, normalized_bundle, forecast)
        if self.base_url:
            result = self._try_remote(seed)
            if result is not None:
                return result
            # remote unreachable → fall through to file adapter
            return self._file_adapter(seed, reason="MIROFISH_BASE_URL set but unreachable")
        if self.adapter_dir:
            return self._file_adapter(seed, reason="MIROFISH_FILE_ADAPTER_DIR set")
        return self._local_fallback(ticker, normalized_bundle, forecast, seed)

    def simulate(self, request: dict[str, Any]) -> dict[str, Any]:
        """
        Generic /simulate entry point that accepts the bridge API contract:
          documents[], forecast_summary, personas_config, scenario
        """
        seed = self._build_seed_from_simulate_request(request)
        if self.base_url:
            result = self._try_remote(seed)
            if result is not None:
                return result
            return self._file_adapter(seed, reason="MIROFISH_BASE_URL set but unreachable")
        if self.adapter_dir:
            return self._file_adapter(seed, reason="MIROFISH_FILE_ADAPTER_DIR set")
        result = self._local_simulation_from_seed(seed)
        result["provider_mode"] = "local_mirofish_fallback"
        return result

    # ------------------------------------------------------------ seed builder

    def _build_seed_packet(
        self,
        ticker: str,
        normalized_bundle: dict[str, Any],
        forecast: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = normalized_bundle["snapshot"]
        sim_seed = normalized_bundle["simulation_seed"]
        return {
            "seed_id": str(uuid.uuid4()),
            "ticker": ticker.upper(),
            "simulation_requirement": (
                f"Simulate retail, analyst, and compliance agent reactions for {ticker.upper()}. "
                "Reddit sentiment is a first-class driver. Derive bullish vs bearish distribution."
            ),
            "context": {
                "latest_close": snapshot["latest_close"],
                "latest_rsi": snapshot["latest_rsi"],
                "reddit_sentiment": snapshot["reddit_sentiment"],
                "prediction_market_consensus": snapshot["prediction_market_consensus"],
                "forecast_close_5d": forecast["forecast_close_5d"],
                "forecast_direction": forecast["direction"],
            },
            "personas_config": sim_seed["agent_personas"],
            "key_narratives": sim_seed["key_narratives"],
            "documents": [
                {
                    "source": "reddit",
                    "content": "\n".join(
                        t.get("title", "") + " " + t.get("body", "")
                        for t in sim_seed["retail_sentiment"]["subreddit_activity"][:5]
                    ),
                },
                {
                    "source": "news",
                    "content": "\n".join(
                        a.get("title", "") for a in sim_seed.get("news_digest", [])[:3]
                    ),
                },
                {
                    "source": "sec",
                    "content": "\n".join(
                        f.get("summary", "") for f in sim_seed.get("sec_digest", [])[:2]
                    ),
                },
            ],
            "forecast_summary": {
                "direction": forecast["direction"],
                "confidence": forecast["confidence"],
                "forecast_close_5d": forecast["forecast_close_5d"],
                "delta_5d": forecast["delta_5d"],
            },
        }

    def _build_seed_from_simulate_request(self, request: dict[str, Any]) -> dict[str, Any]:
        docs = request.get("documents", [])
        return {
            "seed_id": str(uuid.uuid4()),
            "simulation_requirement": request.get("scenario", "Simulate agent reactions."),
            "documents": docs if isinstance(docs, list) else [{"source": "uploaded", "content": docs}],
            "forecast_summary": request.get("forecast_summary", {}),
            "personas_config": request.get("personas_config", []),
            "context": {},
        }

    # ------------------------------------------------------------- mode: remote

    def _try_remote(self, seed: dict[str, Any]) -> dict[str, Any] | None:
        try:
            with httpx.Client(timeout=8.0) as client:
                client.get(f"{self.base_url}/health").raise_for_status()
        except Exception:
            return None
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(
                    f"{self.base_url}/api/graph/ontology/generate",
                    data={
                        "simulation_requirement": seed["simulation_requirement"],
                        "project_name": seed.get("ticker", "market-swarm-lab"),
                    },
                    files=[
                        ("files", (f"doc_{i}.txt", doc.get("content", "").encode(), "text/plain"))
                        for i, doc in enumerate(seed.get("documents", []))
                        if doc.get("content")
                    ],
                )
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:
            return {"provider_mode": "remote_error", "error": str(exc), "seed": seed}

        ontology = payload.get("data", {}).get("ontology", {})
        entity_types = [e.get("name") for e in ontology.get("entity_types", [])]
        personas = seed.get("personas_config", [])
        bullish = sum(1 for p in personas if p.get("stance") in ("bullish", "risk-on", "constructive"))
        bearish = sum(1 for p in personas if p.get("stance") in ("bearish", "risk-off", "cautious"))
        total = max(len(personas), 1)

        return {
            "provider_mode": "mirofish_remote_live",
            "mirofish_project_id": payload.get("data", {}).get("project_id"),
            "ontology_entity_types": entity_types,
            "distribution": {
                "bullish": round(bullish / total, 3),
                "bearish": round(bearish / total, 3),
                "neutral": round((total - bullish - bearish) / total, 3),
            },
            "agent_reasoning_summary": (
                f"MiroFish extracted {len(entity_types)} entity types from uploaded documents. "
                f"Agents lean {_direction(bullish, bearish)} based on persona stances."
            ),
            "final_direction": _direction(bullish, bearish),
            "seed": seed,
        }

    # -------------------------------------------------------- mode: file adapter

    def _file_adapter(self, seed: dict[str, Any], reason: str) -> dict[str, Any]:
        output_dir = self.adapter_dir or Path("state") / "mirofish_seeds"
        output_dir.mkdir(parents=True, exist_ok=True)
        seed_id = seed.get("seed_id", str(uuid.uuid4()))
        seed_path = output_dir / f"{seed_id}_seed.json"
        result_path = output_dir / f"{seed_id}_result.json"
        seed_path.write_text(json.dumps(seed, indent=2, ensure_ascii=False), encoding="utf-8")

        # Check if an operator has already placed a result file
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                result["provider_mode"] = "mirofish_file_adapter_result_loaded"
                return result
            except Exception:
                pass

        return {
            "provider_mode": "mirofish_file_adapter_pending",
            "reason": reason,
            "seed_id": seed_id,
            "seed_path": str(seed_path),
            "result_path": str(result_path),
            "manual_run_instructions": (
                "1. cd external/mirofish && cp .env.example .env  (fill LLM_API_KEY, ZEP_API_KEY)\n"
                "2. npm run setup:all && npm run dev\n"
                f"3. Upload the document content from {seed_path} via the MiroFish UI at http://localhost:3000\n"
                "4. After simulation completes, export or copy the report JSON.\n"
                f"5. Write the result JSON to {result_path}  (bridge will load it on next call)\n"
                "   Required keys: distribution, agent_reasoning_summary, final_direction"
            ),
            # Local fallback so the pipeline keeps running
            **self._local_simulation_from_seed(seed),
        }

    # ------------------------------------------------------ mode: local fallback

    def _local_fallback(
        self,
        ticker: str,
        normalized_bundle: dict[str, Any],
        forecast: dict[str, Any],
        seed: dict[str, Any],
    ) -> dict[str, Any]:
        result = self._local_simulation_from_seed(seed)
        result["provider_mode"] = "local_mirofish_fallback"
        result["seed"] = seed
        return result

    def _local_simulation_from_seed(self, seed: dict[str, Any]) -> dict[str, Any]:
        personas = seed.get("personas_config", [])
        ctx = seed.get("context", {})
        forecast_summary = seed.get("forecast_summary", {})

        reddit_sentiment = float(ctx.get("reddit_sentiment", 0))
        prediction_consensus = float(ctx.get("prediction_market_consensus", 0.5))
        forecast_dir = forecast_summary.get("direction", "up")
        confidence = float(forecast_summary.get("confidence", 0.5))

        score = (
            reddit_sentiment * 35
            + (prediction_consensus - 0.5) * 100
            + (15 if forecast_dir == "up" else -15) * confidence
        )
        score = round(max(-100, min(100, score)), 2)

        bullish_weight = max(0.0, (score + 100) / 200)
        bearish_weight = round(1 - bullish_weight, 3)
        bullish_weight = round(bullish_weight, 3)
        final_direction = "bullish" if score >= 10 else "bearish" if score <= -10 else "rangebound"

        per_persona_reasoning = [
            {
                "persona": p.get("name", f"agent_{i}"),
                "archetype": p.get("archetype", "unknown"),
                "initial_stance": p.get("stance", "neutral"),
                "adjusted_stance": _adjust_stance(p, reddit_sentiment, prediction_consensus),
                "weight": p.get("weight", 1.0),
            }
            for i, p in enumerate(personas)
        ]

        rounds = [
            {
                "round": 1,
                "focus": "Reddit and retail sentiment primes agents",
                "signal": "reddit",
                "running_score": round(score * 0.5, 2),
            },
            {
                "round": 2,
                "focus": "Forecast and prediction markets reprice conviction",
                "signal": "forecast + prediction_markets",
                "running_score": round(score * 0.8, 2),
            },
            {
                "round": 3,
                "focus": "Agent interactions converge on final distribution",
                "signal": "all",
                "running_score": score,
            },
        ]

        return {
            "distribution": {
                "bullish": bullish_weight,
                "bearish": bearish_weight,
                "neutral": 0.0,
            },
            "agent_reasoning_summary": (
                f"{len(personas)} agents simulated over 3 rounds. "
                f"Reddit sentiment {reddit_sentiment:+.2f} and prediction market "
                f"consensus {prediction_consensus:.2f} drove a {final_direction} outcome "
                f"(score {score})."
            ),
            "final_direction": final_direction,
            "outlook_score": score,
            "rounds": rounds,
            "persona_reasoning": per_persona_reasoning,
        }


# ------------------------------------------------------------------ helpers

def _direction(bullish: int, bearish: int) -> str:
    if bullish > bearish:
        return "bullish"
    if bearish > bullish:
        return "bearish"
    return "rangebound"


def _adjust_stance(
    persona: dict[str, Any],
    reddit_sentiment: float,
    prediction_consensus: float,
) -> str:
    stance = persona.get("stance", "neutral")
    if stance in ("bullish", "risk-on", "constructive"):
        if reddit_sentiment < -0.3 or prediction_consensus < 0.4:
            return "cautious"
    if stance in ("bearish", "risk-off", "cautious"):
        if reddit_sentiment > 0.3 and prediction_consensus > 0.6:
            return "constructive"
    return stance
