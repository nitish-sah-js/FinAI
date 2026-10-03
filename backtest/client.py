"""HTTP client for the L1 orchestrator."""
from __future__ import annotations

import asyncio
import time

import httpx

from copilot_common.settings import get_settings

from .events import BacktestCase


async def run_case(case: BacktestCase, llm_mode: str = "local",
                   portfolio: dict | None = None, timeout_s: float = 120.0,
                   base_url: str | None = None) -> tuple[str, dict, list[dict]]:
    """POST /query with as_of, poll GET /runs/{id} every 1 s. Returns (run_id, final, events).

    exclude_holdout=True makes the analog search skip every held-out event (12 §A3.2): without it the backtest
    would be allowed to "find" the very event it is scoring."""
    base = base_url or get_settings().ORCH_URL
    payload = {"query": case.query, "as_of": case.as_of, "llm_mode": llm_mode, "exclude_holdout": True}
    if portfolio is not None:
        payload["portfolio"] = portfolio
    async with httpx.AsyncClient(base_url=base, timeout=30) as c:
        r = await c.post("/query", json=payload)
        r.raise_for_status()
        run_id = r.json()["run_id"]
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            g = await c.get(f"/runs/{run_id}")
            if g.status_code == 200:
                body = g.json()
                if body.get("final") and body.get("status") in ("done", "failed"):
                    return run_id, body["final"], body.get("events", [])
            await asyncio.sleep(1.0)
    raise TimeoutError(f"run {run_id} for {case.event_id} did not finish in {timeout_s}s")
