"""parse_intent: P1 via llm.chat('intent'), with keyword rules as the fallback (05 P1)."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Literal

from pydantic import Field

from copilot_common.models import Intent
from copilot_common.settings import project_root

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

# ---------------- ticker validation ----------------
# NSE/BSE equity symbol; the model sometimes leaks JSON fragments into a ticker string ("BOB.NS'],'ASSET_CLASSES'")
TICKER_RE = re.compile(r"^[A-Z0-9&-]{1,20}(\.(NS|BO))?$")
# indices, futures and FX the tools understand (they fail the equity regex by design)
NON_EQUITY = {"^NSEI", "^NSEBANK", "^BSESN", "^CNXIT", "CL=F", "BZ=F", "NG=F", "GC=F", "INR=X", "^GSPC", "^VIX"}


@lru_cache(maxsize=1)
def _universe() -> tuple[frozenset, dict]:
    """Known NSE symbols (data/nse_symbols.json) and lower-case alias -> ticker (data/ticker_aliases.json + names)."""
    data = project_root() / "data"
    try:
        syms = frozenset(json.loads((data / "nse_symbols.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        syms = frozenset()
    aliases = {k: v for k, v in NAME_TO_TICKER.items()}
    try:
        for t, names in json.loads((data / "ticker_aliases.json").read_text(encoding="utf-8")).items():
            for n in names:
                aliases.setdefault(n.lower(), t)
    except (OSError, ValueError):
        pass
    return syms | {t for t in NAME_TO_TICKER.values() if t.endswith(".NS")}, aliases


def clean_ticker(raw: str) -> str | None:
    """Canonical ticker, or None if it is not a well-formed symbol in the known universe."""
    t = (raw or "").strip()
    if t in NON_EQUITY:
        return t
    t = t.upper()
    if not TICKER_RE.match(t):
        return None
    syms, _ = _universe()
    if not syms:                                  # no symbol file: accept anything well-formed
        return t if "." in t else f"{t}.NS"
    base = t.split(".")[0]
    if t in syms:
        return t
    if t.endswith(".BO") and f"{base}.NS" in syms:
        return t
    if "." not in t and f"{t}.NS" in syms:
        return f"{t}.NS"
    return None


def named_in_query(query: str) -> list[str]:
    """Tickers the user actually named (by symbol or a known company alias)."""
    q = query.lower()
    _, aliases = _universe()
    out = [t for name, t in aliases.items() if re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", q)]
    for tok in re.findall(r"\b[A-Z][A-Z0-9&-]{1,19}(?:\.(?:NS|BO))?\b", query):     # SYMBOLS typed in capitals
        if (c := clean_ticker(tok)) and c not in NON_EQUITY:
            out.append(c)
    return list(dict.fromkeys(out))


def scope_tickers(state: dict, limit: int = 12) -> list[str]:
    """Tickers an agent should look at. Intent tickers are ADDED to the portfolio's, never replace them, unless the
    user named specific stocks in the question (then those stocks are the scope)."""
    query = state["request"]["query"]
    named = [t for t in named_in_query(query) if t not in NON_EQUITY]
    if named:
        return named[:limit]
    held = [h["ticker"] for h in (state.get("portfolio") or {}).get("holdings", [])]
    extra = [t for t in (state.get("intent") or {}).get("tickers", []) if t not in NON_EQUITY]
    return list(dict.fromkeys(held + extra))[:limit]


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
    # keep only well-formed, known symbols; junk from the model is dropped, names in the query are added back
    cleaned = [c for t in intent.tickers if (c := clean_ticker(t))]
    intent.tickers = list(dict.fromkeys(named_in_query(query) + cleaned))[:5]
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
