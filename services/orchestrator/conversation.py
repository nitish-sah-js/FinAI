"""Conversation layer: greetings, small talk, help, out-of-scope and unclear questions, answered BEFORE any LLM or agent.

fast_classify() is pure regex/keyword logic (no model, no network), so these replies arrive in well under a second.
Replies are short templates that never contain numbers, plus three clickable suggestions built from the user's real
holdings and active alerts. Anything with a finance entity in it goes on to the normal agent pipeline.
Also here: follow-up resolution ("why?", "and for ITC?") against the previous run.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone

import httpx

from copilot_common import reachability
from copilot_common.models import FinalAnswer, Intent, QueryRequest, ValidatorReport
from copilot_common.settings import get_settings

from .events import bus
from .ledger import ledger
from .nodes.parse_intent import EVENT_WORDS, named_in_query

# ---------------------------------------------------------------- classification
_INJECTION = re.compile(
    r"ignore (all |any |your |the |previous |prior |above )*(rules|instructions|prompts?|guidelines)|system prompt|"
    r"developer mode|jailbreak|you are now|pretend (to be|you are)|act as (an? )?(unfiltered|unrestricted)|"
    r"disregard (your|all|the) (rules|instructions)|reveal your (prompt|instructions)", re.I)
_GREETING = re.compile(
    r"^(hi+|hii+|hel+o+|hlo|hey+|heya|hiya|howdy|yo|sup|hola|namaste|namaskar|pranam|ram ram|salaam|"
    r"good (morning|afternoon|evening|night|day)|gm|gn|what'?s up|wassup|"
    r"kaise ho|kaisi ho|kya haal( hai| chaal)?|kya chal raha( hai)?)\b", re.I)
_THANKS = re.compile(r"^(thanks|thank you|thank u|thx|ty|tysm|shukriya|dhanyavaad|dhanyavad|dhanyawad|"
                     r"great (job|work)|awesome|nice|cool|perfect|got it|okay thanks|ok thanks)\b", re.I)
_BYE = re.compile(r"^(bye|goodbye|good bye|see (you|ya)|later|cya|alvida|chalo bye|tata)\b", re.I)
_HELP = re.compile(r"\b(help|what can you do|what do you do|who are you|what are you|how (do|can) i use|"
                   r"how does this work|what is this|kya kar sakte ho|tum kaun ho|aap kaun ho|kaise use karu)\b", re.I)
_SMALLTALK = re.compile(r"\b(how are you|how r u|how'?s it going|are you (a )?(bot|robot|ai|human|real)|"
                        r"tell me a joke|i'?m bored|good bot|you'?re (smart|great|dumb)|i love you|"
                        r"tum kaise ho|aap kaise ho)\b", re.I)
_ADVICE = re.compile(
    r"\b(should i (buy|sell|invest|hold|exit|book)|is it (a )?(good|right|bad) time to (buy|sell|invest)|"
    r"which (stocks?|shares?) (to|should i) (buy|sell)|best (stocks?|shares?) to buy|"
    r"stock tips?|multibagger|target price|guaranteed returns?|double my money|"
    r"will (the )?(nifty|sensex|market|[a-z]+) (hit|reach|cross|touch|go to|go above)|"
    r"(kharid(u|un|na|ein)|bech(u|un|na|ein))( chahiye)?|kya (main|mai) .* (kharid|bech))", re.I)
_NON_FINANCE = re.compile(
    r"\b(poem|poetry|song|lyrics|recipe|cook|movie|film|series|cricket|football|ipl|match score|celebrity|"
    r"girlfriend|boyfriend|homework|essay|translate|capital of|python|javascript|code for|write code|"
    r"president of|prime minister of|who won|horoscope|astrology)\b", re.I)
_FINANCE = re.compile(
    r"\b(stocks?|shares?|equit(y|ies)|portfolio|holdings?|positions?|market|nifty|sensex|index|indices|hedge|hedging|"
    r"risk|var|drawdown|exposure|exposed|volatil\w*|crude|oil|brent|rupee|inr|dollar|usd|forex|currency|rbi|repo|"
    r"interest rates?|rate (hike|cut)|inflation|cpi|gdp|bonds?|yields?|monsoon|rain\w*|baarish|barish|cyclone|"
    r"hurricane|typhoon|storm|heat ?wave|garmi|flood|drought|crops?|kharif|rabi|harvest|fmcg|banks?|banking|"
    r"pharma|auto|metals?|energy|power|utilit\w+|agri\w*|sectors?|prices?|earnings|results|dividends?|ipo|fii|dii|"
    r"commodit\w+|gold|silver|weather|scenario|what if|recession|sanctions?|war|budget|tax|policy|"
    r"explain|why did you)\b", re.I)
_FOLLOWUP_WHY = re.compile(r"^\s*(why|why\?|why so|why that|kyun|kyon|kyu|how come|explain( that| this| it)?)\W*$", re.I)
_FOLLOWUP_FOR = re.compile(r"^\s*(and|what about|how about|aur|and for|what of|same for)\b", re.I)
_EMOJI_OR_PUNCT = re.compile(r"^[\W_\d\s]*$", re.UNICODE)


def has_finance_entity(query: str) -> bool:
    return bool(_FINANCE.search(query) or named_in_query(query)
                or any(w in query.lower() for w, _ in EVENT_WORDS))


def fast_classify(query: str, has_context: bool = False) -> str | None:
    """Return a conversational intent, or None when the question should go to the agents."""
    q = re.sub(r"\s+", " ", (query or "")).strip()
    if not q or _EMOJI_OR_PUNCT.match(q):
        return "unclear"
    if _INJECTION.search(q):
        return "out_of_scope"
    words = len(q.split())
    finance = has_finance_entity(q)
    if has_context and (_FOLLOWUP_WHY.match(q) or (_FOLLOWUP_FOR.match(q) and named_in_query(q))):
        return None                                             # follow-up to the previous answer
    if _ADVICE.search(q):
        return "out_of_scope"
    if not finance:
        if _GREETING.match(q) and words <= 8:
            return "greeting"
        if (_THANKS.match(q) or _BYE.match(q)) and words <= 8:
            return "thanks"
        if _HELP.search(q):
            return "help"
        if _SMALLTALK.search(q):
            return "smalltalk"
        if _NON_FINANCE.search(q):
            return "out_of_scope"
        return "unclear"
    if words <= 3 and (_GREETING.match(q) or _THANKS.match(q)) and not named_in_query(q):
        return "greeting" if _GREETING.match(q) else "thanks"
    return None


def is_non_finance(query: str) -> bool:
    return not has_finance_entity(query)


# ---------------------------------------------------------------- suggestions (no numbers, ever)
SECTOR_QUESTIONS = {
    "fmcg": "How does a weak monsoon affect my FMCG holdings?",
    "agri": "What does a poor monsoon mean for my agri holdings?",
    "energy": "What happens to my energy stocks if crude oil jumps?",
    "oil&gas": "What happens to my oil and gas stocks if crude falls?",
    "banks": "How exposed are my bank holdings to an RBI rate hike?",
    "utilities": "Would a heatwave hurt my power utility holdings?",
    "it": "How would a weaker rupee affect my IT stocks?",
    "pharma": "How would a weaker rupee affect my pharma stocks?",
    "metals": "What does a global slowdown mean for my metal stocks?",
    "auto": "How would a crude oil spike hit my auto stocks?",
}
DEFAULT_QUESTIONS = ["Which of my holdings are most exposed to crude oil?", "Run a risk check on my portfolio.",
                     "Summarise what is moving the market today."]
_ALERT_WORDS = {"weather_threshold": "weather", "agri_stress": "crop stress", "news_burst": "news",
                "sentiment_shift": "sentiment", "price_z": "price move", "volume_z": "trading volume"}


async def active_alerts(timeout_s: float = 0.4) -> list[dict]:
    """Unacknowledged tier 2-3 alerts from the monitor; [] if it does not answer within timeout_s."""
    url = get_settings().MONITOR_URL.rstrip("/") + "/alerts"
    if reachability.is_down(url):
        return []
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await asyncio.wait_for(c.get(url, params={"tier_min": 2, "limit": 10}), timeout_s)
            return [a for a in r.json() if not a.get("acknowledged")]
    except Exception:  # noqa: BLE001
        reachability.mark_down(url)
        return []


def _name(ticker: str) -> str:
    return ticker.replace(".NS", "").replace(".BO", "")


def suggestions(holdings: list[dict], alerts: list[dict], query: str = "", focus: list[str] | None = None) -> list[str]:
    out: list[str] = []
    for a in alerts[:1]:
        tick = next((_name(t) for t in a.get("tickers", []) if not re.search(r"\d", t)), None)
        what = _ALERT_WORDS.get(a.get("kind"), "latest")
        if tick:
            out.append(f"What does the {what} alert mean for {tick}?")
    for t in focus or []:
        out.append(f"What are the main risks for {_name(t)} in my portfolio?")
    for h in holdings:
        q = SECTOR_QUESTIONS.get((h.get("sector") or "").lower())
        if q:
            out.append(q)
    out += DEFAULT_QUESTIONS
    out = [s for s in dict.fromkeys(out) if not re.search(r"\d", s)]           # never a number in a suggestion
    if query:                                                                  # closest to what was asked first
        qw = set(re.findall(r"[a-z]+", query.lower()))
        out.sort(key=lambda s: -len(qw & set(re.findall(r"[a-z]+", s.lower()))))
    return out[:3]


# ---------------------------------------------------------------- replies (templates, no numbers)
CAN_DO = ("I look at how events such as a cyclone, a weak monsoon, a crude oil move or an RBI decision could affect "
          "your holdings, and suggest hedges you can paper trade. Every figure I give is checked against its source.")
CAN_DO_HI = ("Main dekhta hoon ki cyclone, kamzor monsoon, crude ka move ya RBI ka faisla aapke holdings par kya asar "
             "daal sakta hai, aur hedge suggest karta hoon. Har figure source se check hota hai.")


def _hinglish(req: QueryRequest) -> bool:
    return req.lang in ("hi", "hinglish") or bool(re.search(r"\b(kaise|kya|haal|shukriya|dhanyavad|namaste|aap|tum)\b",
                                                          req.query, re.I))


def reply_text(intent: str, req: QueryRequest) -> str:
    hi = _hinglish(req)
    q = req.query.strip()
    if intent == "greeting":
        return ("Namaste! " + CAN_DO_HI + " Neeche se koi sawaal chuniye, ya apna poochhiye."
                if hi else "Hello! " + CAN_DO + " Pick a question below or ask your own.")
    if intent == "thanks":
        if _BYE.match(q):
            return "Phir milenge! Jab chahiye, main yahin hoon." if hi else "Goodbye! I'll keep watching your holdings for alerts."
        return "Aapka swagat hai! Aur kuch dekhna ho to poochhiye." if hi else "You're welcome! Ask me anything else about your holdings."
    if intent == "help":
        return ("Main Sigma hoon, aapka market copilot. " + CAN_DO_HI if hi else
                "I'm Sigma, a market copilot for your portfolio. " + CAN_DO +
                " Try one of the questions below.")
    if intent == "smalltalk":
        return ("Main theek hoon, shukriya! Markets par nazar rakh raha hoon. " if hi else
                "I'm doing well, thanks for asking, and keeping an eye on the markets. ") + (
                "Kya dekhein?" if hi else "Want to look at something together?")
    if intent == "out_of_scope":
        if _INJECTION.search(q):
            return ("I can't change how I work, but I'm happy to help with your portfolio. " + CAN_DO)
        if _ADVICE.search(q):
            return ("I can't tell you what to buy or sell, or where the market will go. This is decision support, "
                    "not trading advice. What I can do is show the risks, how an event could hit your holdings, "
                    "and how exposed you are. Try one of these:")
        return ("That's outside what I can help with. I focus on markets and your portfolio. " + CAN_DO)
    # unclear
    return ("I'm not sure I got that. " + (CAN_DO_HI if hi else CAN_DO) +
            (" Shayad aap yeh poochhna chahte the:" if hi else " Did you mean one of these?"))


async def answer(run_id: str, req: QueryRequest, portfolio: dict, intent_name: str, t0: float) -> dict:
    """Publish a conversational FinalAnswer (no agents, no numbers) through the normal run stream."""
    await bus.emit(run_id, "parse_intent", "started", message="checking what kind of message this is")
    alerts = await active_alerts()
    focus = named_in_query(req.query) if intent_name == "out_of_scope" else []
    sugg = suggestions(portfolio.get("holdings", []), alerts,
                       req.query if intent_name in ("unclear", "out_of_scope") else "", focus)
    text = reply_text(intent_name, req)
    intent = Intent(intent=intent_name, references_portfolio=False, needs_tools=[])
    ms = int((time.perf_counter() - t0) * 1000)
    await bus.emit(run_id, "parse_intent", "finished", latency_ms=ms,
                   message=f"{intent_name.replace('_', ' ')}: answered directly, no agents needed",
                   meta={"intent": intent.model_dump(mode="json"), "fast_path": True})
    await bus.publish_activity({"type": "pet_reaction", "data": {
        "run_id": run_id, "reaction": "wave" if intent_name in ("greeting", "thanks", "help", "smalltalk") else "confused"}})
    final = FinalAnswer(run_id=run_id, query=req.query, intent=intent, bottom_line=text, holdings_impact=[], hedges=[],
                        confidence="high", what_could_be_wrong=[], red_team=None,
                        validator=ValidatorReport(numbers_found=0, numbers_matched=0, unmatched=[], action="pass"),
                        signals=[], evidence=[], answer_markdown=text, lang=req.lang, llm_usage={},
                        latency_ms={"parse_intent": ms, "total": ms}, kind="conversation", suggestions=sugg)
    out = final.model_dump(mode="json")
    await ledger.save_final(run_id, out)
    await bus.publish_final(run_id, out)
    return out


# ---------------------------------------------------------------- follow-ups
_EVENT_PHRASE = {"rates": "An RBI rate change", "oil": "A crude oil price move", "monsoon": "A weak monsoon",
                 "cyclone": "A cyclone", "hurricane": "A hurricane", "heatwave": "A heatwave", "policy": "A policy change"}


def swap_stocks(prev_query: str, who: str) -> str:
    """'How is ITC doing?' + 'HDFCBANK' -> 'How is HDFCBANK doing?'. The stocks the previous question named are
    replaced (first mention) or removed (later mentions); if it named none, the new stocks are appended as the focus."""
    from .nodes.parse_intent import NON_EQUITY, _universe
    old = [t for t in named_in_query(prev_query) if t not in NON_EQUITY]      # stocks only; keep "crude", "Nifty"
    if not old:
        return f"{prev_query} Focus on {who}."
    _, aliases = _universe()
    names = sorted({n for n, t in aliases.items() if t in old} | {_name(t).lower() for t in old}, key=len, reverse=True)
    pat = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(n) for n in names) + r")(?![A-Za-z0-9])", re.I)
    first = [True]

    def sub(m: re.Match) -> str:
        if first[0]:
            first[0] = False
            return who
        return ""
    out = re.sub(r"\s{2,}", " ", pat.sub(sub, prev_query)).strip()
    return out if who in out else f"{prev_query} Focus on {who}."


async def resolve_followup(req: QueryRequest) -> QueryRequest:
    """Rewrite a short follow-up using the previous run (req.ref_run_id): 'why?' -> explain that answer;
    'and for ITC?' -> the previous event, asked for ITC. Anything else is returned unchanged."""
    if not req.ref_run_id:
        return req
    q = req.query.strip()
    if _FOLLOWUP_WHY.match(q):
        return req.model_copy(update={"query": "Explain your last answer: why did you say that?"})
    from .nodes.parse_intent import NON_EQUITY
    names = [t for t in named_in_query(q) if t not in NON_EQUITY]
    if _FOLLOWUP_FOR.match(q) and names and len(q.split()) <= 8:
        prev = await ledger.get_run(req.ref_run_id)
        final = (prev or {}).get("final") or {}
        it = final.get("intent") or {}
        lead = _EVENT_PHRASE.get(it.get("event_type") or "")
        region = f" in {it['region']}" if it.get("region") and lead else ""
        who = ", ".join(_name(t) for t in names)
        if lead:
            new_q = f"{lead}{region}: what happens to {who}?"
        elif prev:
            new_q = swap_stocks(prev["query"], who)
        else:
            return req
        return req.model_copy(update={"query": new_q})
    return req


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
