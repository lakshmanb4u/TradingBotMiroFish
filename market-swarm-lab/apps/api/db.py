from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import psycopg
import redis

POSTGRES_DSN = os.getenv("POSTGRES_DSN", "")
REDIS_URL = os.getenv("REDIS_URL", "")


def init_infra() -> None:
    if POSTGRES_DSN:
        try:
            with psycopg.connect(POSTGRES_DSN) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        create table if not exists run_reports (
                            id serial primary key,
                            ticker text not null,
                            created_at timestamptz not null,
                            overall_score numeric,
                            json_path text not null,
                            markdown_path text not null,
                            provider_modes jsonb not null
                        )
                        """
                    )
                conn.commit()
        except Exception:
            pass


def cache_report(ticker: str, payload: dict[str, Any]) -> None:
    if not REDIS_URL:
        return
    try:
        client = redis.from_url(REDIS_URL, decode_responses=True)
        client.setex(f"market-swarm-lab:{ticker.upper()}", 3600, json.dumps(payload))
    except Exception:
        pass


def persist_run_summary(ticker: str, payload: dict[str, Any]) -> None:
    if not POSTGRES_DSN:
        return
    try:
        report = payload.get("report", {})
        simulation = payload.get("simulation", {})
        with psycopg.connect(POSTGRES_DSN) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into run_reports (
                        ticker, created_at, overall_score, json_path, markdown_path, provider_modes
                    ) values (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        ticker.upper(),
                        datetime.utcnow(),
                        simulation.get("outlook_score"),
                        report.get("json_path", ""),
                        report.get("markdown_path", ""),
                        json.dumps(payload.get("provider_modes", {})),
                    ),
                )
            conn.commit()
    except Exception:
        pass
