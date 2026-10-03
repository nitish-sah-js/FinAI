"""The 6 fan-out agents (04 §2). Each: HTTP tool call(s) on L2/L3 → staleness → narrator (P4–P7) or code summary.

Every agent is async and awaits I/O only, so the Send branches really run in parallel. Nothing here raises:
timeouts and failures become a degraded signal and the run continues.
"""
from __future__ import annotations

from datetime import date
from typing import Awaitable, Callable

from copilot_common.models import AgentSignal, Evidence, ToolResult

from .. import tools_client as tools
from ..budget import Budget, budget, timeout_for
from ..events import bus
from ..prompt_loader import render
from ..regions import resolve
from ..staleness import apply_staleness, effective_confidence
from .common import as_of, chaos, chat, finish_kwargs

AGENT_HOST = {"sentiment_agent": "L2", "weather_agent": "L3", "agri_agent": "L2", "macro_agent": "L3",
              "analog_agent": "L2", "exposure_agent": "L2"}
PROMPT = {"weather_agent": "P4", "agri_agent": "P5", "macro_agent": "P6", "analog_agent": "P7"}


def _mean_conf(evs: list[Evidence]) -> float | None:
    vals = [c for c in (effective_confidence(e.model_dump()) for e in evs) if c is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def code_signal(agent: str, evs: list[Evidence], signal: str = "n/a", note: str = "") -> AgentSignal:
    """Template signal used when the narrator LLM is unavailable: evidence summaries + citations, no new numbers."""
    parts = [f"{e.summary or e.tool} [{e.id}]" for e in evs[:3]]
    summary = "; ".join(parts) or "No evidence available."
    if any(e.degraded for e in evs) and "degraded" not in summary:
        summary += ". Some evidence is degraded."
    if note:
        summary += f" ({note})"
    return AgentSignal(agent=agent, signal=signal, summary=summary, evidence_ids=[e.id for e in evs],
                       confidence=_mean_conf(evs), degraded=any(e.degraded for e in evs))


def exposed_holdings(portfolio: dict) -> list[dict]:
    return [{"ticker": h["ticker"], "sector": h.get("sector")} for h in portfolio.get("holdings", [])]


async def narrate(s: dict, agent: str, b: Budget, evs: list[Evidence]) -> AgentSignal | None:
    prompt = render(PROMPT[agent], evidence_json=[_compact(e) for e in evs],
                    exposed_holdings=exposed_holdings(s["portfolio"]))
    res = await chat(s, agent, b, "narrator", [{"role": "user", "content": prompt}], schema=AgentSignal,
                     fixture=f"narrator_{agent}", timeout_s=max(1.0, timeout_for(agent) - b.ms / 1000))
    if not res.ok or res.parsed is None:
        return None
    sig: AgentSignal = res.parsed
    ids = {e.id for e in evs}
    sig.agent = agent
    sig.evidence_ids = [i for i in sig.evidence_ids if i in ids] or sorted(ids)
    sig.confidence = _mean_conf(evs)          # confidence comes from the evidence, not from the LLM
    sig.degraded = any(e.degraded for e in evs)
    return sig


def _compact(e: Evidence) -> dict:
    return {"id": e.id, "tool": e.tool, "value": e.value, "confidence": e.confidence,
            "degraded": e.degraded, "as_of": e.as_of.isoformat()[:16]}


async def _run_agent(s: dict, agent: str, work: Callable[[Budget], Awaitable[tuple[list[Evidence], AgentSignal]]]) -> dict:
    run_id = s["run_id"]
    await bus.emit(run_id, agent, "started", host=AGENT_HOST[agent])
    evs: list[Evidence] = []
    b = Budget(agent)
    try:
        async with budget(agent) as b:
            evs, sig = await work(b)
    except TimeoutError:
        sig = code_signal(agent, evs, note="time budget exceeded")
        sig.degraded = True
    except Exception as e:  # noqa: BLE001
        sig = code_signal(agent, evs, note=f"error: {type(e).__name__}")
        sig.degraded = True
    degraded = sig.degraded or any(e.degraded for e in evs)
    msg = sig.summary[:140]
    reasons = sorted({e.degraded_reason for e in evs if e.degraded and e.degraded_reason})
    if reasons:
        msg = f"degraded ({', '.join(reasons)}) · " + msg
    await bus.emit(run_id, agent, "degraded" if degraded else "finished", host=AGENT_HOST[agent],
                   **finish_kwargs(b, evidence_ids=[e.id for e in evs], message=msg, signal=sig.signal))
    return {"evidence": [e.model_dump(mode="json") for e in evs], "signals": [sig.model_dump(mode="json")],
            "latency": {agent: b.ms}}


def _kw(s: dict, agent: str) -> dict:
    return {"run_id": s["run_id"], "chaos": chaos(s), "timeout_s": timeout_for(agent)}


def _situation(s: dict) -> str:
    i = s["intent"]
    return (f"{i.get('event_type') or 'market event'} {i.get('region') or ''}. {s['request']['query']}").strip()


# ---------------- agents ----------------
async def sentiment_agent(s: dict) -> dict:
    async def work(b: Budget):
        i = s["intent"]
        tickers = i.get("tickers") or [h["ticker"] for h in s["portfolio"].get("holdings", [])]
        news_tr: ToolResult = await tools.news(_situation(s), tickers, as_of=as_of(s), **_kw(s, "sentiment_agent"))
        items = next((e.value.get("items", []) for e in news_tr.evidence if e.tool == "news"), [])
        sent_tr = await tools.sentiment(items, s["portfolio"], as_of=as_of(s), **_kw(s, "sentiment_agent"))
        evs = apply_staleness(news_tr.evidence + sent_tr.evidence)
        sent = next((e for e in evs if e.tool == "sentiment"), None)
        ps = sent.value.get("portfolio_sentiment") if sent else None
        if ps is None:
            return evs, code_signal("sentiment_agent", evs)
        label = "bearish" if ps < -0.15 else "bullish" if ps > 0.15 else "neutral"
        tone = "negative" if ps < -0.15 else "positive" if ps > 0.15 else "neutral"
        summary = (f"Portfolio-weighted news tone is {tone} ({ps:+.2f}) [{sent.id}] across "
                   f"{len(items)} headlines [{evs[0].id}]. Sentiment measures tone, not a forecast.")
        return evs, AgentSignal(agent="sentiment_agent", signal=label, summary=summary, evidence_ids=[e.id for e in evs],
                                confidence=_mean_conf(evs), degraded=any(e.degraded for e in evs))
    return await _run_agent(s, "sentiment_agent", work)


async def weather_agent(s: dict) -> dict:
    async def work(b: Budget):
        region_id, _ = resolve(s["intent"].get("region"), s["request"]["query"])
        tr = await tools.weather(region_id, s["intent"].get("horizon_days", 5), as_of=as_of(s), **_kw(s, "weather_agent"))
        evs = apply_staleness(tr.evidence)
        return evs, (await narrate(s, "weather_agent", b, evs)) or code_signal("weather_agent", evs, "mixed")
    return await _run_agent(s, "weather_agent", work)


async def agri_agent(s: dict) -> dict:
    async def work(b: Budget):
        _, agri_id = resolve(s["intent"].get("region"), s["request"]["query"])
        if agri_id is None:                       # no crop model covers this region: say so, call nothing
            sig = code_signal("agri_agent", [], "n/a", "no crop-stress model covers this region")
            sig.summary = "No crop-stress model covers this region, so there is no agri signal."
            return [], sig
        on = str(as_of(s) or date.today())
        tr = await tools.agri(agri_id, on, as_of=as_of(s), **_kw(s, "agri_agent"))
        evs = apply_staleness(tr.evidence)
        return evs, (await narrate(s, "agri_agent", b, evs)) or code_signal("agri_agent", evs, "mixed")
    return await _run_agent(s, "agri_agent", work)


async def macro_agent(s: dict) -> dict:
    async def work(b: Budget):
        tr = await tools.macro(as_of=as_of(s), **_kw(s, "macro_agent"))
        evs = apply_staleness(tr.evidence)
        return evs, (await narrate(s, "macro_agent", b, evs)) or code_signal("macro_agent", evs, "neutral")
    return await _run_agent(s, "macro_agent", work)


async def analog_agent(s: dict) -> dict:
    async def work(b: Budget):
        i = s["intent"]
        assets = (i.get("tickers") or [h["ticker"] for h in s["portfolio"].get("holdings", [])])[:8] + ["^NSEI"]
        horizon = "1d" if i.get("horizon_days", 5) <= 1 else "20d" if i.get("horizon_days", 5) >= 15 else "5d"
        tr = await tools.analogs(_situation(s), i.get("event_type"), i.get("region"), assets, horizon,
                                 as_of=as_of(s), exclude_holdout=bool(s["request"].get("exclude_holdout")),
                                 **_kw(s, "analog_agent"))
        evs = apply_staleness(tr.evidence)
        return evs, (await narrate(s, "analog_agent", b, evs)) or code_signal("analog_agent", evs, "mixed")
    return await _run_agent(s, "analog_agent", work)


async def exposure_agent(s: dict) -> dict:
    async def work(b: Budget):
        tr = await tools.exposure(s["portfolio"], as_of=as_of(s), **_kw(s, "exposure_agent"))
        evs = apply_staleness(tr.evidence)
        ev = next((e for e in evs if e.tool == "exposure"), None)
        if ev is None or not ev.value.get("by_sector"):
            return evs, code_signal("exposure_agent", evs)
        top = sorted(ev.value["by_sector"].items(), key=lambda kv: -kv[1])[:3]
        sectors = ", ".join(f"{k} {v * 100:.0f}%" for k, v in top)
        sens = [t["ticker"] for t in ev.value.get("by_ticker", []) if (t.get("weather_sens") or 0) >= 0.5]
        summary = f"Largest sector weights: {sectors} [{ev.id}]."
        if sens:
            summary += f" Most weather-sensitive holdings: {', '.join(sens)} [{ev.id}]."
        return evs, AgentSignal(agent="exposure_agent", signal="n/a", summary=summary, evidence_ids=[e.id for e in evs],
                                confidence=_mean_conf(evs), degraded=any(e.degraded for e in evs))
    return await _run_agent(s, "exposure_agent", work)


AGENTS = {"sentiment_agent": sentiment_agent, "weather_agent": weather_agent, "agri_agent": agri_agent,
          "macro_agent": macro_agent, "analog_agent": analog_agent, "exposure_agent": exposure_agent}
