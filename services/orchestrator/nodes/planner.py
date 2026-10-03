"""planner (optional, P2): may call ≤2 extra tools when the router left a gap (04 §2, 05 P2)."""
from __future__ import annotations

import re

from .. import tools_client as tools
from ..budget import MAX_STEPS, budget
from ..events import bus
from ..prompt_loader import render
from ..staleness import apply_staleness
from .common import as_of, chaos, chat, finish_kwargs

PLANNER_TOOLS = [
    {"type": "function", "function": {
        "name": "event_study", "description": "Abnormal returns of one ticker around a past event date.",
        "parameters": {"type": "object", "properties": {"ticker": {"type": "string"},
                                                        "event_date": {"type": "string", "description": "YYYY-MM-DD"}},
                       "required": ["ticker", "event_date"]}}},
    {"type": "function", "function": {
        "name": "scenario", "description": "Portfolio P&L for factor shocks. Keys: crude, usd_inr, nifty, monsoon_rain "
                                           "(percent points), repo_bps (basis points).",
        "parameters": {"type": "object", "properties": {"shocks": {"type": "object"}}, "required": ["shocks"]}}},
]
GAP_WORDS = re.compile(r"event study|last time|historically|in the past|pichhli baar|what if|agar", re.I)


def has_gap(state: dict) -> bool:
    """Gap = the query names tickers outside the portfolio, or asks for history / what-if beyond the router's plan."""
    held = {h["ticker"] for h in state["portfolio"].get("holdings", [])}
    outside = [t for t in state["intent"].get("tickers", []) if t not in held]
    return bool(outside) or bool(GAP_WORDS.search(state["request"]["query"]))


async def planner(state: dict) -> dict:
    run_id = state["run_id"]
    await bus.emit(run_id, "planner", "started", message="checking for coverage gaps")
    evidence = state.get("evidence", [])
    done = sorted({e["tool"] for e in evidence})
    new_evs = []
    steps = state.get("steps", 0)
    try:
        async with budget("planner") as b:
            prompt = render("P2", done_tools=done, evidence_summaries=[{"id": e["id"], "summary": e.get("summary")}
                                                                       for e in evidence][:12],
                            query=state["request"]["query"])
            res = await chat(state, "planner", b, "planner", [{"role": "user", "content": prompt}], tools=PLANNER_TOOLS)
            for call in (res.tool_calls if res.ok else [])[:2]:
                if steps >= MAX_STEPS:
                    break
                steps += 1
                args = call.get("arguments") or {}
                kw = {"run_id": run_id, "chaos": chaos(state), "timeout_s": 8}
                if call["name"] == "event_study" and args.get("ticker") and args.get("event_date"):
                    tr = await tools.event_study(args["ticker"], args["event_date"], as_of=as_of(state), **kw)
                elif call["name"] == "scenario" and isinstance(args.get("shocks"), dict):
                    tr = await tools.scenario(state["portfolio"], args["shocks"], as_of=as_of(state), **kw)
                else:
                    continue
                new_evs += apply_staleness(tr.evidence)
    except TimeoutError:
        pass
    msg = f"{len(new_evs)} extra tool call(s)" if new_evs else "SUFFICIENT: no extra tools"
    await bus.emit(run_id, "planner", "finished", **finish_kwargs(b, [e.id for e in new_evs], msg))
    return {"evidence": [e.model_dump(mode="json") for e in new_evs], "steps": steps, "latency": {"planner": b.ms}}
