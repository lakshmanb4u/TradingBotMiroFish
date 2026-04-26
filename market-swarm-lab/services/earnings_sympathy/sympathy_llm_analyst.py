"""SympathyLLMAnalyst — LLM veto/explain layer for top deterministic candidates.

The LLM role is strictly bounded:
  - CAN: explain why a candidate is interesting, flag narrative mismatch, veto
  - CANNOT: create new candidates, override hard filters, change strike/expiry, turn SKIP → BUY

Input: top 3 candidates from deterministic scorer (only called if >=2 pass hard filters).
Output per candidate: {vetoed, veto_reason, narrative_summary, risks}

If LLM call fails for any reason: continue in degraded mode (llm_status.degraded_mode=true).
Uses env vars: LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME (same as rest of project).
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

_log = logging.getLogger(__name__)

try:
    import httpx
    _HTTPX = True
except ImportError:
    _HTTPX = False


def _llm_env() -> tuple[str, str, str]:
    key   = os.environ.get("LLM_API_KEY", "")
    base  = os.environ.get("LLM_BASE_URL", "https://api.moonshot.cn/v1")
    model = os.environ.get("LLM_MODEL_NAME", "moonshot-v1-8k")
    return key, base, model


_SYSTEM_PROMPT = """You are a senior options trading analyst reviewing pre-earnings sympathy trade candidates.
A deterministic scoring engine has already applied hard filters and ranked candidates.
Your role is ONLY to:
1. Explain why each candidate is interesting (or not)
2. Detect obvious narrative mismatches between the reporter's earnings and the sympathy ticker
3. Veto candidates where the numbers are right but the narrative is clearly wrong
4. Summarize the key risk factors

You CANNOT:
- Create new candidates
- Override hard filter decisions
- Change the strike price or expiry
- Turn a SKIP into a BUY

Respond in valid JSON only. No markdown, no extra text."""


def _build_prompt(candidates: list[dict], reporter_context: str) -> str:
    summaries = []
    for i, c in enumerate(candidates[:3], 1):
        summaries.append(
            f"Candidate {i}: {c['sympathy_ticker']} {c['option_type']} "
            f"${c['strike']} exp {c['expiry']} (DTE {c['dte']}) "
            f"premium ${c['premium']} | final_score={c['final_score']} | "
            f"hist_avg_move={c.get('hist_avg_1d_move_pct','?')}% | "
            f"positioning={c['positioning_score']} convexity={c['convexity_score']} "
            f"technical={c['technical_score']} | "
            f"trigger=${c.get('trigger_level','?')} invalidation=${c.get('invalidation_level','?')} | "
            f"reason: {c['reason']}"
        )

    candidates_text = "\n".join(summaries)

    return f"""Reporter earnings context: {reporter_context}

Deterministic top candidates:
{candidates_text}

For each candidate, return a JSON array with one object per candidate:
[
  {{
    "candidate_index": 1,
    "sympathy_ticker": "...",
    "vetoed": false,
    "veto_reason": "",
    "narrative_summary": "Why this trade makes sense given the earnings context",
    "risks": ["risk 1", "risk 2"]
  }}
]"""


class SympathyLLMAnalyst:
    """LLM veto and explanation layer. Gracefully degrades if unavailable."""

    def __init__(self, timeout: int = 15) -> None:
        self._timeout = timeout
        self._key, self._base, self._model = _llm_env()
        self._available = bool(self._key) and _HTTPX

    def analyze(
        self,
        candidates: list[dict],
        reporter: str,
        reporter_context: str = "",
    ) -> dict[str, Any]:
        """Analyze top candidates. Returns analysis dict + llm_status.

        Args:
            candidates: top passing candidates (max 3 sent to LLM)
            reporter: e.g. "INTC"
            reporter_context: free-text summary of what the reporter announced
        """
        if not candidates:
            return {
                "llm_analyses": [],
                "llm_status": {"degraded_mode": False, "reason": "no_candidates"},
            }

        top = candidates[:3]

        if not self._available:
            _log.info("[llm_analyst] LLM unavailable (no key or httpx) — degraded mode")
            return self._degraded(top, "llm_not_configured")

        if not reporter_context:
            reporter_context = f"{reporter} has upcoming earnings. Analyze sympathy impact on peers."

        prompt = _build_prompt(top, reporter_context)

        try:
            raw = self._call_llm(prompt)
            analyses = self._parse_response(raw, top)
            _log.info("[llm_analyst] LLM analyzed %d candidates for %s", len(analyses), reporter)
            return {
                "llm_analyses": analyses,
                "llm_status": {"degraded_mode": False, "model": self._model},
            }
        except Exception as exc:
            _log.warning("[llm_analyst] LLM call failed: %s — degraded mode", exc)
            return self._degraded(top, str(exc))

    def _call_llm(self, user_prompt: str) -> str:
        url = self._base.rstrip("/") + "/chat/completions"
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 1024,
        }
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    def _parse_response(self, raw: str, candidates: list[dict]) -> list[dict]:
        """Parse LLM JSON response. Fall back to neutral analysis on parse error."""
        # Strip markdown fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

        try:
            analyses = json.loads(text)
            if not isinstance(analyses, list):
                raise ValueError("expected list")
            # Validate + sanitize each entry
            result = []
            for item in analyses:
                result.append({
                    "candidate_index": int(item.get("candidate_index", 0)),
                    "sympathy_ticker": str(item.get("sympathy_ticker", "")),
                    "vetoed": bool(item.get("vetoed", False)),
                    "veto_reason": str(item.get("veto_reason", "")),
                    "narrative_summary": str(item.get("narrative_summary", "")),
                    "risks": [str(r) for r in item.get("risks", [])],
                })
            return result
        except Exception as exc:
            _log.warning("[llm_analyst] parse error: %s — returning neutral analysis", exc)
            return [
                {
                    "candidate_index": i + 1,
                    "sympathy_ticker": c["sympathy_ticker"],
                    "vetoed": False,
                    "veto_reason": "",
                    "narrative_summary": f"LLM parse error — deterministic score stands ({c['final_score']})",
                    "risks": ["llm_parse_failed"],
                }
                for i, c in enumerate(candidates)
            ]

    def _degraded(self, candidates: list[dict], reason: str) -> dict[str, Any]:
        return {
            "llm_analyses": [
                {
                    "candidate_index": i + 1,
                    "sympathy_ticker": c["sympathy_ticker"],
                    "vetoed": False,
                    "veto_reason": "",
                    "narrative_summary": f"LLM unavailable — deterministic score {c['final_score']}",
                    "risks": [],
                }
                for i, c in enumerate(candidates)
            ],
            "llm_status": {"degraded_mode": True, "reason": reason},
        }
