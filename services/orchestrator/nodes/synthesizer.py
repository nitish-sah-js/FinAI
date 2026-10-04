"""synthesizer (P8 / P8_hi): the final cited answer. Falls back to a code template that only reuses evidence numbers."""
from __future__ import annotations

import json
import re

from ..budget import budget, timeout_for
from ..events import bus
from ..portfolio import summary as portfolio_summary
from ..prompt_loader import render
from .common import chat, finish_kwargs, run_mode

LANG_NAME = {"hi": "Hindi", "hinglish": "Hinglish"}
_JSON_TAIL = re.compile(r"<json>(.*?)</json>", re.S)
_FIRST_SECTION = re.compile(r"^###\s*(Bottom line|Saar)\b", re.M | re.I)


def _by_tool(evidence: list[dict], tool: str) -> dict | None:
    return next((e for e in evidence if e["tool"] == tool
                 and not (isinstance(e.get("value"), dict) and e["value"].get("status") == "unavailable")), None)


# The crop-stress model does not beat a "no change" baseline overall (services/agri/MODEL_CARD.md), so its signal is
# low weight: it never leads the bottom line, and any answer that uses it carries this dated caveat.
LOW_WEIGHT_AGENTS = {"agri_agent"}
AGRI_CAVEAT = ("Crop-stress model caveat (evaluated 2026-10-04): it does not beat a 'no change' forecast overall "
               "(accuracy 0.60 vs 0.62) and catches about a third of real deteriorations; treat it as an early warning only.")


def _agri_used(state: dict) -> bool:
    return any(s.get("agent") == "agri_agent" and s.get("signal") not in (None, "n/a") for s in state.get("signals", []))


def synth_inputs(state: dict) -> dict:
    ev = state.get("evidence", [])
    analogs, risk, hedge = _by_tool(ev, "analogs"), _by_tool(ev, "risk"), _by_tool(ev, "hedge")
    return {
        "query": state["request"]["query"],
        "portfolio_summary": portfolio_summary(state["portfolio"]),
        "signals_json": [{**{k: s.get(k) for k in ("agent", "signal", "summary", "evidence_ids", "confidence", "degraded")},
                          **({"weight": "low", "caveat": AGRI_CAVEAT} if s.get("agent") in LOW_WEIGHT_AGENTS else {})}
                         for s in state.get("signals", [])],
        "distribution_json": analog_lines(analogs) or ["no analog evidence"],
        "risk_json": (risk_lines(risk) + scenario_range_lines(ev)) or ["no risk evidence"],
        "hedges_json": (hedge_lines(hedge) + validation_lines(_by_tool(ev, "hedge_validation"))) or ["No hedge computed."],
        "unavailable_json": unavailable_lines(ev) or ["none"],
    }


def unavailable_lines(evidence: list[dict]) -> list[str]:
    """One plain line per tool whose service could not be reached (tools_client.unavailable_result)."""
    out = []
    for e in evidence:
        if isinstance(e.get("value"), dict) and e["value"].get("status") == "unavailable":
            out.append(e.get("summary") or f"No {e['tool']} data.")
    return list(dict.fromkeys(out))


# Small models copy finished, cited sentences far more reliably than they cite raw JSON fields.
def _pct(x) -> str:
    return f"{x * 100:+.1f}%"


def _proxy_note(d: dict) -> str:
    """Vector DB fills a missing ticker from a same-group proxy (08 §6.6): say so, never present it as the ticker's own."""
    px = d.get("proxies_used") or []
    return f" (via proxy {', '.join(px)})" if px else ""


def analog_lines(ev: dict | None) -> list[str]:
    if not ev:
        return []
    v = ev["value"]
    n = len(v.get("analogs", []))
    out = [f"{n} historical analogs matched (confidence {v.get('confidence')}) [{ev['id']}]."]
    for d in v.get("distribution", []):
        if d.get("p10") is None:
            continue
        kind = "abnormal return" if d.get("measure") == "abnormal" else "return"
        out.append(f"{d['asset']}{_proxy_note(d)}: past {d.get('horizon', '')} {kind} ranged {_pct(d['p10'])} to "
                   f"{_pct(d['p90'])} (median {_pct(d.get('median') or 0)}) [{ev['id']}].")
    return out


def risk_lines(ev: dict | None) -> list[str]:
    if not ev or ev["value"].get("var_inr") is None:
        return []
    v = ev["value"]
    line = f"{v.get('horizon_days')}-day {round((v.get('confidence_level') or 0.95) * 100)}% VaR is {_fmt_inr(v['var_inr'])}"
    if v.get("var_pct") is not None:
        line += f" ({v['var_pct'] * 100:.1f}% of the portfolio)"
    if v.get("cvar_inr") is not None:
        line += f"; expected shortfall {_fmt_inr(v['cvar_inr'])}"
    return [line + f" [{ev['id']}]."]


def hedge_lines(ev: dict | None) -> list[str]:
    if not ev:
        return []
    out = []
    for p in ev["value"].get("proposals", []):
        unit = p["unit"].rstrip("s") if p["quantity"] == 1 and p["unit"] in ("lots", "shares") else p["unit"]
        line = f"{p['side'].title()} {p['quantity']:g} {unit} {p['instrument']} (hedge ratio {p['hedge_ratio']}"
        if p.get("est_cost_inr") is not None:
            line += f", est. cost {_fmt_inr(p['est_cost_inr'])}"
        line += f") [{ev['id']}]"
        if ev["value"].get("post_var_inr") is not None:
            line += f", which cuts VaR to {_fmt_inr(ev['value']['post_var_inr'])} [{ev['id']}]"
        out.append(line + ".")
    return out


def scenario_range_lines(evidence: list[dict]) -> list[str]:
    """Evidence-based scenario (quant /scenario/from_evidence): median and p10/p90 P&L, every number cited."""
    ev = next((e for e in evidence if e["tool"] == "scenario" and "cases" in (e.get("value") or {})), None)
    if not ev:
        return []
    c = ev["value"]["cases"]
    pn = sorted(x["pnl_inr"] for x in c.values())
    if pn[-1] - pn[0] < 1:
        return [f"If recent macro moves persist, the portfolio P&L is {_fmt_inr(c['median']['pnl_inr'])} "
                f"(point estimate, no analog range) [{ev['id']}]."]
    return [f"If the past analog moves repeat, the portfolio P&L is {_fmt_inr(c['median']['pnl_inr'])} in the median "
            f"case (range {_fmt_inr(pn[0])} to {_fmt_inr(pn[-1])}) [{ev['id']}]."]


def validation_lines(ev: dict | None) -> list[str]:
    """Out-of-sample hedge back-check on past analog events (quant /hedge_validation)."""
    if not ev or not (ev.get("value") or {}).get("n"):
        return []
    v = ev["value"]
    lo, hi = v["range_dd_reduction_pp"]
    line = (f"Back-check: on {v['n']} past analog events this hedge cut the worst drawdown in {v['n_improved']} of them "
            f"(median {v['median_dd_reduction_pp']:+.1f} pp, range {lo:+.1f} to {hi:+.1f} pp) [{ev['id']}]")
    if v["n"] < 5:
        line += "; too few events to be more than a sanity check"
    return [line + "."]


_BARE_TAIL = re.compile(r"\n\s*(\{[^\n]*\"(?:confidence|holdings_impact)\"[\s\S]*\})\s*$")


def split_answer(text: str) -> tuple[str, dict | None]:
    """Split markdown from the JSON tail: <json>…</json>, or a bare trailing JSON object (small models drop the tags)."""
    from copilot_llm.json_repair import extract_json
    text = text or ""
    m = _JSON_TAIL.search(text) or _BARE_TAIL.search(text)
    tail = None
    if m:
        tail = extract_json(m.group(1))
        text = text[:m.start()].rstrip()
    return dedupe_lines(text.strip()), tail if isinstance(tail, dict) else None


def dedupe_lines(md: str) -> str:
    """Remove exact repeats of a non-empty line (4B models occasionally loop one bullet dozens of times)."""
    seen: set[str] = set()
    out = []
    for line in md.split("\n"):
        key = line.strip()
        if key and not key.startswith("#") and key in seen:
            continue
        seen.add(key)
        out.append(line)
    return "\n".join(out)


def _fmt_inr(x) -> str:
    return f"₹{x:,.0f}" if isinstance(x, (int, float)) else str(x)


def code_answer(state: dict) -> str:
    """Deterministic fallback answer. Every number is copied from evidence with its id, so the validator passes."""
    ev = state.get("evidence", [])
    sigs = state.get("signals", [])
    lines = ["### Bottom line"]
    lead = [s for s in sigs if s["agent"] in ("weather_agent", "analog_agent", "macro_agent")][:2]   # agri: low weight
    lines.append(" ".join(s["summary"] for s in lead) or "Not enough evidence for a confident view.")
    lines += ["", "### Impact on your holdings"]
    analogs = _by_tool(ev, "analogs")
    held = {h["ticker"] for h in state["portfolio"].get("holdings", [])}
    for d in (analogs or {}).get("value", {}).get("distribution", []):
        if d.get("asset") in held and d.get("p10") is not None:
            lines.append(f"- **{d['asset']}**{_proxy_note(d)}: past analogs ranged from {d['p10'] * 100:+.1f}% to {d['p90'] * 100:+.1f}% "
                         f"over {d.get('horizon', '')} [{analogs['id']}].")
    for s in sigs:
        if s["agent"] in ("sentiment_agent", "exposure_agent"):
            lines.append(f"- {s['summary']}")
    risk = _by_tool(ev, "risk")
    if risk and risk["value"].get("var_inr") is not None:
        lines.append(f"- **Portfolio**: VaR {_fmt_inr(risk['value']['var_inr'])} [{risk['id']}].")
    lines += [f"- {x}" for x in scenario_range_lines(ev)]
    lines += ["", "### Suggested hedges"]
    hl = hedge_lines(_by_tool(ev, "hedge")) + validation_lines(_by_tool(ev, "hedge_validation"))
    lines += [f"- {h}" for h in hl] or ["- No hedge computed."]
    lines += ["", "### Confidence and what could be wrong"]
    degraded = [s["agent"] for s in sigs if s.get("degraded")]
    lines.append("- Written from a template because the language model was unavailable.")
    lines += [f"- {x}" for x in unavailable_lines(ev)]
    if _agri_used(state):
        lines.append(f"- {AGRI_CAVEAT}")
    if degraded:
        lines.append(f"- Degraded inputs: {', '.join(degraded)}.")
    lines += ["", "_Decision support, not a trading signal._"]
    return "\n".join(lines)


async def synthesizer(state: dict) -> dict:
    run_id = state["run_id"]
    lang = state["request"].get("lang", "en")
    await bus.emit(run_id, "synthesizer", "started", message=f"writing the answer ({lang})")
    inputs = synth_inputs(state)
    if lang == "en":
        prompt = render("P8", **inputs)
    else:
        prompt = render("P8_hi", lang_name=LANG_NAME.get(lang, "Hinglish"), **inputs)
    # Hindi locally → gemma3 (better at Hindi) via the narrator chain; otherwise the synthesizer chain (cloud boost)
    role = "narrator" if lang != "en" and run_mode(state) == "local" else "synthesizer"
    draft, tail, status = "", None, "finished"
    try:
        async with budget("synthesizer") as b:
            res = await chat(state, "synthesizer", b, role, [{"role": "user", "content": prompt}],
                             timeout_s=timeout_for("synthesizer"))
            m = _FIRST_SECTION.search(res.text or "") if res.ok else None
            if m:                                   # drop any preamble / leaked reasoning before the first section
                draft, tail = split_answer(res.text[m.start():])
    except TimeoutError:
        pass
    if not draft:
        draft, status = code_answer(state), "degraded"
    await bus.emit(run_id, "synthesizer", status,
                   **finish_kwargs(b, message="answer drafted" if status == "finished" else "template answer (LLM unavailable)"))
    return {"draft": draft, "answer_json": tail or {}, "latency": {"synthesizer": b.ms}}
