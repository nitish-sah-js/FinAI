"""parse_intent: P1 via llm.chat('intent'), with keyword rules as the fallback (05 P1)."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field

from copilot_common.models import Intent

from ..budget import budget
from ..events import bus
from ..prompt_loader import render
from .common import chat, finish_kwargs

NAME_TO_TICKER = {"reliance": "RELIANCE.NS", "ongc": "ONGC.NS", "itc": "ITC.NS", "hul": "HINDUNILVR.NS",
                  "hindustan unilever": "HINDUNILVR.NS", "coal india": "COALINDIA.NS", "ntpc": "NTPC.NS",
                  "hdfc bank": "HDFCBANK.NS", "upl": "UPL.NS", "adani ports": "ADANIPORTS.NS", "bpcl": "BPCL.NS",
                  "tata power": "TATAPOWER.NS", "infosys": "INFY.NS", "tcs": "TCS.NS", "sbi": "SBIN.NS",
                  "nifty": "^NSEI", "bank nifty": "^NSEBANK", "crude": "CL=F", "natural gas": "NG=F"}
EVENT_WORDS = [("cyclone", "cyclone"), ("hurricane", "hurricane"), ("typhoon", "cyclone"), ("monsoon", "monsoon"),
               ("baarish", "monsoon"), ("barish", "monsoon"), ("rainfall", "monsoon"), ("heatwave", "heatwave"),
               ("heat wave", "heatwave"), ("garmi", "heatwave"), ("repo", "rates"), ("rbi", "rates"),
               ("rate hike", "rates"), ("rate cut", "rates"), ("crude", "oil"), ("brent", "oil"), ("oil", "oil"),
               ("export ban", "policy"), ("budget", "policy"), ("policy", "policy"), ("crop", "monsoon"),
               ("kharif", "monsoon"), ("rabi", "monsoon")]
REGIONS = ["odisha", "gujarat", "kutch", "chennai", "tamil nadu", "andhra", "west bengal", "maharashtra", "vidarbha",
           "mumbai", "madhya pradesh", "punjab", "rajasthan", "delhi", "north india", "gulf", "louisiana", "texas",
           "bay of bengal", "india"]

ToolName = Literal["sentiment", "weather", "agri", "macro", "analogs", "exposure", "risk", "hedge"]


class IntentLLM(Intent):
    """What the model must emit: same fields as Intent, but bounded lists. The grammar Ollama builds from this schema
    stops the 4B model enumerating every bank ticker until it hits max_tokens (seen in evals, case 6)."""
    tickers: list[str] = Field(default_factory=list, max_length=5)
    asset_classes: list[str] = Field(default_factory=list, max_length=4)
    needs_tools: list[ToolName] = Field(default_factory=list, max_length=8)


def keyword_intent(query: str) -> Intent:
    q = query.lower()
    event_type = next((et for w, et in EVENT_WORDS if w in q), None)
    if re.search(r"\bexplain\b|why did you|samjhao", q):
        intent = "explain"
    elif "hedge" in q or "protect" in q:
        intent = "hedge_request"
    elif re.search(r"\bvar\b|value at risk|\brisk\b|drawdown", q):
        intent = "portfolio_risk"
    elif re.search(r"summar|market today|how is the market", q) and not event_type:
        intent = "market_summary"
    elif event_type:
        intent = "event_impact"
    else:
        intent = "market_summary"
    if re.search(r"\btoday\b|\baaj\b|1[- ]day", q):
        horizon = 1
    elif re.search(r"month|mahine", q):
        horizon = 20
    else:
        horizon = 5
    tickers = sorted({t for name, t in NAME_TO_TICKER.items() if re.search(rf"\b{re.escape(name)}\b", q)})
    region = next((r.title() for r in REGIONS if r in q), None)
    tools = {"event_impact": ["weather", "analogs", "exposure", "sentiment"],
             "portfolio_risk": ["exposure", "risk"], "hedge_request": ["exposure", "hedge"],
             "market_summary": ["sentiment", "macro"], "explain": []}[intent]
    if event_type in {"monsoon", "heatwave"}:
        tools.append("agri")
    if event_type in {"oil", "rates", "policy"}:
        tools.append("macro")
    return Intent(intent=intent, event_type=event_type, region=region, tickers=tickers, asset_classes=["equity"],
                  horizon_days=horizon, references_portfolio=bool(re.search(r"\bmy\b|\bmere\b|\bmera\b|portfolio|holding", q)),
                  needs_tools=tools)


def normalise(intent: Intent, query: str) -> Intent:
    intent = Intent.model_validate(intent.model_dump())          # IntentLLM → plain Intent
    intent.horizon_days = max(1, min(int(intent.horizon_days or 5), 60))
    tickers = [t.strip().upper() if not t.startswith("^") else t.strip() for t in intent.tickers]
    intent.tickers = list(dict.fromkeys(t for t in tickers if t and t.lower() not in {"null", "none", "n/a"}))[:5]
    q = query.lower()
    if re.search(r"\bexplain\b|why did you", q) and intent.intent != "explain":
        intent.intent = "explain"
    # unambiguous horizon words beat the model's guess
    if re.search(r"\b1[- ]day\b|\btoday\b|\baaj\b", q):
        intent.horizon_days = 1
    elif re.search(r"\b(this|next) month\b|\bmahine\b", q):
        intent.horizon_days = 20
    # needs_tools only ADDS agents (04 §2); fill the obvious ones the 4B model often omits
    tools = list(intent.needs_tools)
    add = []
    if intent.intent == "event_impact":
        add.append("analogs")
    if intent.intent == "portfolio_risk" or re.search(r"\bvar\b|value at risk", q):
        add.append("risk")
    if intent.intent == "hedge_request":
        add.append("hedge")
    if intent.event_type in {"monsoon", "heatwave"} or re.search(r"kharif|rabi|crop|ndvi|monsoon", q):
        add.append("agri")
    if intent.event_type in {"rates", "oil", "policy"}:
        add.append("macro")
    intent.needs_tools = list(dict.fromkeys(tools + add))
    return intent


async def parse_intent(state: dict) -> dict:
    run_id, query = state["run_id"], state["request"]["query"]
    await bus.emit(run_id, "parse_intent", "started", message="parsing the query")
    status, note = "finished", ""
    try:
        async with budget("parse_intent") as b:
            res = await chat(state, "parse_intent", b, "intent",
                             [{"role": "system", "content": "You convert market questions into strict JSON."},
                              {"role": "user", "content": render("P1", query=query)}], schema=IntentLLM)
            intent = res.parsed if res.ok else None
            if not res.ok and res.fallbacks and all(("unreachable" in f or "down" in f or "timeout" in f)
                                                    for f in res.fallbacks):
                note = "no LLM reachable"
    except TimeoutError:
        intent = None
        note = "timeout"
    if intent is None:
        intent, status = keyword_intent(query), "degraded"
        note = note or "LLM output invalid"
    intent = normalise(intent, query)
    msg = f"{intent.intent} · {intent.event_type or '-'} · {intent.region or '-'} · {intent.horizon_days}d"
    if status == "degraded":
        msg += f" (keyword fallback: {note})"
    await bus.emit(run_id, "parse_intent", status, **finish_kwargs(b, message=msg, intent=intent.model_dump(mode="json")))
    return {"intent": intent.model_dump(mode="json"), "latency": {"parse_intent": b.ms}}
