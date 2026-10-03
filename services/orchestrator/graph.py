"""LangGraph wiring (04 §2) + run_graph() / resume_run(), which build and publish the FinalAnswer.

START → parse_intent → router ──Send──► 6 agents (parallel) → join → [planner] → quant_agent
      → synthesizer → red_team → validator → END            (intent "explain" → explain → END)
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from copilot_common.ids import new_run_id
from copilot_common.models import (AgentSignal, Evidence, FinalAnswer, HedgeProposal, Intent, QueryRequest,
                                   RedTeamReport, ValidatorReport)
from copilot_llm import llm, usage_for

from . import portfolio as portfolios
from .budget import RUN_DEADLINE_S
from .events import bus
from .ledger import ledger
from .nodes.agents import AGENTS
from .nodes.explain import explain
from .nodes.join import join
from .nodes.parse_intent import keyword_intent, parse_intent
from .nodes.planner import has_gap, planner
from .nodes.quant_agent import quant_agent
from .nodes.red_team import red_team
from .nodes.synthesizer import code_answer, synthesizer
from .nodes.validator import validate, validator
from .router import AGENT_NODES, select_agents
from .staleness import effective_confidence
from .state import RunState


# ---------------- router node + edges ----------------
async def router(state: RunState) -> dict:
    run_id = state["run_id"]
    intent = Intent.model_validate(state["intent"])
    agents = select_agents(intent, state.get("portfolio"), state["request"]["query"])
    await bus.emit(run_id, "router", "finished", latency_ms=0,
                   message=("explain → replay" if intent.intent == "explain" else "fan-out: " + ", ".join(agents)),
                   meta={"agents": agents})
    for a in AGENT_NODES:
        if a not in agents and intent.intent != "explain":
            await bus.emit(run_id, a, "skipped", message="not needed for this intent")
    bus.mark(run_id, "fanout_start")
    return {"selected_agents": agents}


def route_agents(state: RunState):
    if state["intent"]["intent"] == "explain":
        return "explain"
    agents = state.get("selected_agents") or []
    if not agents:
        return "join"
    return [Send(a, {"run_id": state["run_id"], "intent": state["intent"], "portfolio": state["portfolio"],
                     "request": state["request"]}) for a in agents]


def after_join(state: RunState) -> str:
    return "planner" if has_gap(state) else "quant_agent"


def build_graph(checkpointer=None):
    g = StateGraph(RunState)
    g.add_node("parse_intent", parse_intent)
    g.add_node("router", router)
    for name, fn in AGENTS.items():
        g.add_node(name, fn)
    g.add_node("join", join)
    g.add_node("planner", planner)
    g.add_node("quant_agent", quant_agent)
    g.add_node("synthesizer", synthesizer)
    g.add_node("red_team", red_team)
    g.add_node("validator", validator)
    g.add_node("explain", explain)

    g.add_edge(START, "parse_intent")
    g.add_edge("parse_intent", "router")
    g.add_conditional_edges("router", route_agents, AGENT_NODES + ["explain", "join"])
    for a in AGENT_NODES:
        g.add_edge(a, "join")
    g.add_conditional_edges("join", after_join, ["planner", "quant_agent"])
    g.add_edge("planner", "quant_agent")
    g.add_edge("quant_agent", "synthesizer")
    g.add_edge("synthesizer", "red_team")
    g.add_edge("red_team", "validator")
    g.add_edge("validator", END)
    g.add_edge("explain", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def set_graph(graph) -> None:
    global _graph
    _graph = graph


# ---------------- FinalAnswer ----------------
_ORDER = {"low": 0, "medium": 1, "high": 2}


def _min_conf(*vals: str | None) -> str:
    vals = [v for v in vals if v in _ORDER]
    return min(vals, key=_ORDER.get) if vals else "low"


def _dedupe(evidence: list[dict]) -> list[Evidence]:
    seen, out = set(), []
    for e in evidence:
        if e["id"] not in seen:
            seen.add(e["id"])
            out.append(Evidence.model_validate(e))
    return out


def _section(md: str, *headings: str) -> str:
    for h in headings:
        m = re.search(rf"###\s*{re.escape(h)}[^\n]*\n+(.*?)(?=\n###|\Z)", md, re.S | re.I)
        if m and m.group(1).strip():
            return m.group(1).strip().split("\n\n")[0].strip()
    first = next((ln for ln in md.splitlines() if ln.strip() and not ln.startswith("#")), "")
    return first.strip()


def _code_confidence(evidence: list[Evidence], red: dict | None, validator_rep: dict | None) -> str:
    vals = [c for c in (effective_confidence(e.model_dump()) for e in evidence) if c is not None]
    mean = sum(vals) / len(vals) if vals else 0.0
    cap = "high" if mean >= 0.7 else "medium" if mean >= 0.45 else "low"
    if any(e.degraded and e.degraded_reason != "mock" for e in evidence):
        cap = _min_conf(cap, "medium")
    analogs = next((e for e in evidence if e.tool == "analogs"), None)
    if analogs and analogs.value.get("confidence") == "low":
        cap = _min_conf(cap, "medium")
    if red and red.get("verdict") == "do not act":
        cap = "low"
    if validator_rep and validator_rep.get("action") == "stripped":
        cap = "low"
    return cap


def _holdings_impact(state: dict, evidence: list[Evidence], tail: dict) -> list[dict]:
    items = tail.get("holdings_impact") if isinstance(tail.get("holdings_impact"), list) else []
    items = [i for i in items if isinstance(i, dict) and i.get("ticker")]
    if items:
        return items
    held = {h["ticker"] for h in state["portfolio"].get("holdings", [])}
    analogs = next((e for e in evidence if e.tool == "analogs"), None)
    out = []
    for d in (analogs.value.get("distribution", []) if analogs else []):
        if d.get("asset") in held and d.get("p10") is not None:
            med = d.get("median") or 0
            out.append({"ticker": d["asset"], "impact": "negative" if med < -0.002 else "positive" if med > 0.002 else "mixed",
                        "range": f"{d['p10'] * 100:+.1f}% to {d['p90'] * 100:+.1f}% ({d.get('horizon', '')})",
                        "evidence_ids": [analogs.id]})
    return out


def _hedges(evidence: list[Evidence]) -> list[HedgeProposal]:
    out = []
    for e in evidence:
        if e.tool != "hedge":
            continue
        for p in e.value.get("proposals", []):
            try:
                hp = HedgeProposal.model_validate(p)
            except ValueError:
                continue
            if e.id not in hp.evidence_ids:
                hp.evidence_ids.append(e.id)
            out.append(hp)
    return out


def build_final(state: dict, total_ms: int) -> FinalAnswer:
    run_id = state["run_id"]
    req = state["request"]
    intent = Intent.model_validate(state.get("intent") or keyword_intent(req["query"]).model_dump())
    usage = usage_for(run_id).to_dict(llm.quota.snapshot())
    latency = {**(state.get("latency") or {}), "total": total_ms}

    if intent.intent == "explain":
        ex = state.get("explanation") or {}
        md = ex.get("explanation_markdown") or "No explanation available."
        ref = ex.get("ref_final") or {}
        return FinalAnswer(run_id=run_id, query=req["query"], intent=intent, bottom_line=_section(md),
                           holdings_impact=[], hedges=[], confidence=ref.get("confidence") or "medium",
                           what_could_be_wrong=[], red_team=None,
                           validator=ValidatorReport(numbers_found=0, numbers_matched=0, unmatched=[], action="pass"),
                           signals=[], evidence=[], answer_markdown=md, lang=req.get("lang", "en"),
                           llm_usage=usage, latency_ms=latency)

    evidence = _dedupe(state.get("evidence", []))
    signals = [AgentSignal.model_validate(s) for s in state.get("signals", [])]
    md = state.get("answer_markdown")
    validator_rep = state.get("validator")
    if md is None:                      # run stopped before the validator (deadline / failure)
        draft = state.get("draft") or code_answer({**state, "evidence": [e.model_dump(mode="json") for e in evidence]})
        rep, md = validate(draft, [e.model_dump(mode="json") for e in evidence], intent.horizon_days, req["query"])
        validator_rep = rep.model_dump()
    tail = state.get("answer_json") or {}
    red = state.get("red_team")
    llm_conf = tail.get("confidence") if tail.get("confidence") in _ORDER else None
    confidence = _min_conf(llm_conf or "high", _code_confidence(evidence, red, validator_rep))
    wrong = [w for w in tail.get("what_could_be_wrong", []) if isinstance(w, str)] or (red or {}).get("reasons", [])
    return FinalAnswer(
        run_id=run_id, query=req["query"], intent=intent,
        bottom_line=_section(md, "Bottom line", "Saar"),
        holdings_impact=_holdings_impact(state, evidence, tail), hedges=_hedges(evidence), confidence=confidence,
        what_could_be_wrong=wrong, red_team=RedTeamReport.model_validate(red) if red else None,
        validator=ValidatorReport.model_validate(validator_rep), signals=signals, evidence=evidence,
        answer_markdown=md, lang=req.get("lang", "en"), llm_usage=usage, latency_ms=latency)


# ---------------- run / resume ----------------
def _config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}, "recursion_limit": 60}


async def _finish(run_id: str, state: dict, t0: float, status: str) -> dict:
    final = build_final(state, int((time.perf_counter() - t0) * 1000)).model_dump(mode="json")
    await ledger.save_final(run_id, final)
    if status != "done":
        await ledger.set_status(run_id, status)
    await bus.publish_final(run_id, final)
    return final


async def _invoke(run_id: str, payload: Any, deadline_s: float) -> tuple[dict, str]:
    graph = get_graph()
    try:
        state = await asyncio.wait_for(graph.ainvoke(payload, _config(run_id)), deadline_s)
        return state, "done"
    except Exception as e:  # noqa: BLE001  (TimeoutError included) → partial answer from the last checkpoint
        await bus.emit(run_id, "validator", "failed", message=f"run stopped: {type(e).__name__}: {e}"[:200])
        snap = await graph.aget_state(_config(run_id))
        return dict(snap.values), "failed"


async def run_graph(request: QueryRequest, run_id: str | None = None, deadline_s: float = RUN_DEADLINE_S) -> dict:
    """Run one query end to end and return the FinalAnswer dict. Importable (the backtest calls it directly)."""
    run_id = run_id or new_run_id()
    bus.start_run(run_id)
    t0 = time.perf_counter()
    if await ledger.get_run(run_id) is None:
        await ledger.create_run(run_id, request.query, request.model_dump(mode="json"))
    pf = request.portfolio or portfolios.load("demo")
    init: RunState = {"run_id": run_id, "request": request.model_dump(mode="json"), "portfolio": pf.model_dump(mode="json"),
                      "evidence": [], "signals": [], "errors": [], "steps": 0, "latency": {}}
    state, status = await _invoke(run_id, init, deadline_s)
    state.setdefault("run_id", run_id)
    state.setdefault("request", init["request"])
    state.setdefault("portfolio", init["portfolio"])
    return await _finish(run_id, state, t0, status)


async def resume_run(run_id: str, deadline_s: float = RUN_DEADLINE_S) -> dict:
    """Continue a failed run from its last checkpoint; finished nodes are not re-run."""
    run = await ledger.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    bus.forget(run_id)               # drop the old in-memory final so WS subscribers wait for the new one
    bus.start_run(run_id)
    t0 = time.perf_counter()
    await ledger.set_status(run_id, "running")
    await bus.emit(run_id, "router", "progress", message="resuming from checkpoint")
    state, status = await _invoke(run_id, None, deadline_s)
    return await _finish(run_id, state, t0, status)
