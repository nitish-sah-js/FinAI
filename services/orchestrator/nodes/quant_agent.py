"""quant_agent: risk / hedge / scenario on L2 in parallel after the join. Pure code, no LLM (04 §2)."""
from __future__ import annotations

import asyncio

from copilot_common.models import Intent

from .. import tools_client as tools
from ..budget import budget, timeout_for
from ..events import bus
from ..router import quant_plan
from ..staleness import apply_staleness
from .common import as_of, chaos, finish_kwargs

SHOCK_SOURCES = {"analogs", "macro", "agri", "weather"}     # evidence the quant scenario builder can turn into shocks


def shock_evidence(evidence: list[dict]) -> list[dict]:
    """Evidence for /scenario/from_evidence: id + tool + value only (the builder reads values, never LLM text)."""
    return [{"id": e["id"], "tool": e["tool"], "value": e.get("value") or {}, "degraded": bool(e.get("degraded")),
             "degraded_reason": e.get("degraded_reason")} for e in evidence
            if e.get("tool") in SHOCK_SOURCES and e.get("value")]


def analog_events(evidence: list[dict]) -> list[dict]:
    """Past analog events (id + date) for the out-of-sample hedge back-check."""
    an = next((e for e in evidence if e.get("tool") == "analogs"), None)
    return [{"event_id": a.get("event_id"), "event_date": a["event_date"]}
            for a in ((an or {}).get("value") or {}).get("analogs", []) if a.get("event_date")]


async def quant_agent(state: dict) -> dict:
    run_id = state["run_id"]
    intent = Intent.model_validate(state["intent"])
    plan = quant_plan(intent, state["request"]["query"])
    await bus.emit(run_id, "quant_agent", "started", host="L2",
                   message="running " + ", ".join(k for k, v in plan.items() if v))
    kw = {"run_id": run_id, "chaos": chaos(state), "timeout_s": timeout_for("quant_agent")}
    pf, h = state["portfolio"], intent.horizon_days
    jobs = {"risk": tools.risk(pf, h, as_of=as_of(state), **kw)}
    if plan["hedge"]:
        jobs["hedge"] = tools.hedge(pf, h, as_of=as_of(state), **kw)
    if plan["scenario"]:
        jobs["scenario"] = tools.scenario(pf, plan["scenario"], as_of=as_of(state), **kw)
    prior = state.get("evidence", [])
    if shock_evidence(prior):
        jobs["scenario_evidence"] = tools.scenario_from_evidence(pf, shock_evidence(prior), "5d" if h <= 10 else "20d",
                                                                 as_of=as_of(state), **kw)
    if plan["hedge"] and analog_events(prior):
        jobs["hedge_validation"] = tools.hedge_validation(pf, analog_events(prior), h, as_of=as_of(state), **kw)
    evs, quant = [], {}
    try:
        async with budget("quant_agent") as b:
            results = await asyncio.gather(*jobs.values())
        for name, tr in zip(jobs, results):
            got = apply_staleness(tr.evidence)
            evs += got
            if got:
                quant[name] = got[0].id
    except TimeoutError:
        pass
    degraded = any(e.degraded for e in evs) or len(quant) < len(jobs)
    risk = next((e for e in evs if e.tool == "risk"), None)
    msg = ", ".join(e.summary or e.tool for e in evs) if evs else "quant engine unavailable"
    await bus.emit(run_id, "quant_agent", "degraded" if degraded else "finished", host="L2",
                   **finish_kwargs(b, [e.id for e in evs], msg[:160],
                                   var_inr=(risk.value.get("var_inr") if risk else None)))
    return {"evidence": [e.model_dump(mode="json") for e in evs], "quant": quant, "latency": {"quant_agent": b.ms}}
