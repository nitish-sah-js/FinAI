"""Deterministic intent → agents table (04 §2). The LLM never decides the plan."""
from __future__ import annotations

import re

from copilot_common.models import Intent

from .regions import is_agri_region

AGENT_NODES = ["sentiment_agent", "weather_agent", "agri_agent", "macro_agent", "analog_agent", "exposure_agent"]
TOOL_TO_AGENT = {"sentiment": "sentiment_agent", "weather": "weather_agent", "agri": "agri_agent",
                 "macro": "macro_agent", "analogs": "analog_agent", "exposure": "exposure_agent"}

ALWAYS = {
    "event_impact": ["weather_agent", "analog_agent", "exposure_agent", "sentiment_agent"],
    "portfolio_risk": ["exposure_agent", "sentiment_agent", "macro_agent"],
    "hedge_request": ["exposure_agent", "macro_agent", "analog_agent"],
    "market_summary": ["sentiment_agent", "macro_agent"],
    "explain": [],
}
WEATHER_SENSITIVE_SECTORS = {"energy", "utilities", "power", "agri", "fmcg", "cement", "ports", "mining", "oil&gas"}
STORM_WORDS = ("storm", "cyclone", "hurricane", "heat", "rain", "flood", "monsoon")


def select_agents(intent: Intent, portfolio: dict | None, query: str = "") -> list[str]:
    if intent.intent == "explain":
        return []
    agents = list(ALWAYS[intent.intent])
    et = intent.event_type
    if intent.intent == "event_impact":
        if et in {"monsoon", "heatwave", "cyclone"} or is_agri_region(intent.region, query):
            agents.append("agri_agent")
        if et in {"oil", "rates", "policy"}:
            agents.append("macro_agent")
    elif intent.intent == "portfolio_risk":
        sectors = {(h.get("sector") or "").lower() for h in (portfolio or {}).get("holdings", [])}
        if sectors & WEATHER_SENSITIVE_SECTORS:          # proxy for weather_sens > 0.3 before exposure has run
            agents.append("weather_agent")
    elif intent.intent == "market_summary":
        if et in {"cyclone", "hurricane", "heatwave", "monsoon"} or any(w in query.lower() for w in STORM_WORDS):
            agents.append("weather_agent")
    for tool in intent.needs_tools:                       # needs_tools can ADD agents, never remove
        a = TOOL_TO_AGENT.get(tool)
        if a:
            agents.append(a)
    seen: set[str] = set()
    return [a for a in AGENT_NODES if a in agents and not (a in seen or seen.add(a))]


_SHOCK = re.compile(
    r"(?P<factor>crude|oil|brent|inr|rupee|usd/?inr|nifty|market|repo|rates?|monsoon|rain(?:fall)?)"
    r"[^.;\d+\-−]{0,25}?(?P<sign>[+\-−]|up|down|falls?|rises?|drops?|jumps?)?\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>%|bps|bp)",
    re.I)
_FACTOR = {"crude": "crude", "oil": "crude", "brent": "crude", "inr": "usd_inr", "rupee": "usd_inr", "usdinr": "usd_inr",
           "usd/inr": "usd_inr", "nifty": "nifty", "market": "nifty", "repo": "repo_bps", "rate": "repo_bps",
           "rates": "repo_bps", "monsoon": "monsoon_rain", "rain": "monsoon_rain", "rainfall": "monsoon_rain"}
_NEG = {"-", "−", "down", "fall", "falls", "drop", "drops"}


def parse_shocks(query: str) -> dict[str, float]:
    """'if crude +10% and repo up 25bps' → {"crude": 10, "repo_bps": 25}. Units as in 06 ScenarioReq."""
    shocks: dict[str, float] = {}
    for m in _SHOCK.finditer(query):
        factor = _FACTOR.get(m.group("factor").lower().replace(" ", ""))
        if not factor:
            continue
        val = float(m.group("num"))
        if (m.group("sign") or "").lower() in _NEG:
            val = -val
        if factor == "repo_bps" and m.group("unit") == "%":
            val *= 100
        shocks[factor] = val
    return shocks


def quant_plan(intent: Intent, query: str) -> dict:
    """Which quant tools run after the join (04 §2)."""
    return {"risk": True,
            "hedge": intent.intent in {"hedge_request", "event_impact"},
            "scenario": parse_shocks(query) or None}
