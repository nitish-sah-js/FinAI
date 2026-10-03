"""HTTP client for the L1 orchestrator."""
from __future__ import annotations
import asyncio, os, time
import httpx
from .events import BacktestCase

ORCH_URL = os.getenv("ORCH_URL", "http://localhost:8000")


async def run_case(case: BacktestCase, llm_mode: str = "local",
                   portfolio: dict | None = None, timeout_s: float = 120.0,
                   base_url: str | None = None) -> tuple[str, dict, list[dict]]:
    """POST /query with as_of, poll GET /runs/{id} every 1 s. Returns (run_id, final, events)."""
    base = base_url or ORCH_URL
    payload = {"query": case.query, "as_of": case.as_of, "llm_mode": llm_mode}
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
                if body.get("final"):
                    return run_id, body["final"], body.get("events", [])
            await asyncio.sleep(1.0)
    raise TimeoutError(f"run {run_id} for {case.event_id} did not finish in {timeout_s}s")
