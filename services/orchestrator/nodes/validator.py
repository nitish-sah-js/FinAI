"""Numbers Ledger validator (04 §6). Pure code, no LLM: every figure in the answer must trace to cited evidence."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from copilot_common.settings import get_settings
from copilot_common.models import ValidatorReport

from ..events import bus

NUM = re.compile(r"(?<![\w.])[-+−]?₹?\d(?:[\d,]*\d)?(?:\.\d+)?\s?(?:%|σ|bps\b|bp\b|cr\b|crore\b|lakh\b|x\b)?")
CITE = re.compile(r"\[(ev_[a-z_]+_\d{3})\]")
BAD_CITE = re.compile(r"\[ev_[^\]\s]*\]")          # any [ev_...] token, well-formed or not
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?Z?)?\b")
SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+(?=[A-Z*_\-(\[])")
LIST_MARKER = re.compile(r"^\s*(?:\d+[.)])\s")
JSON_TAIL = re.compile(r"<json>.*?</json>", re.S)


@dataclass
class Found:
    raw: str
    values: list[float]
    line_idx: int
    sent_idx: int


def _flatten(x, out: list[float]) -> None:
    if isinstance(x, bool) or x is None:
        return
    if isinstance(x, (int, float)):
        out.append(float(x))
    elif isinstance(x, str) and len(x) <= 80:            # instrument names, titles: "NIFTY OCT 24500 PE buy"
        out.extend(float(n) for n in re.findall(r"(?<![\w.])\d+(?:\.\d+)?", x.replace(",", "")))
    elif isinstance(x, dict):
        for v in x.values():
            _flatten(v, out)
    elif isinstance(x, (list, tuple)):
        out.append(float(len(x)))            # counts like "4 analogs" / "3 headlines"
        for v in x:
            _flatten(v, out)


def pool_for(evidence: list[dict]) -> list[float]:
    base: list[float] = []
    for ev in evidence:
        _flatten(ev.get("value"), base)
        if ev.get("confidence") is not None:
            base.append(float(ev["confidence"]))
    out = set()
    for v in base:
        for d in (v, v * 100, v / 100):
            for a in (d, abs(d)):
                out.update({a, round(a), round(a, 1), round(a, 2)})
    return list(out)


def _normalise(raw: str) -> list[float] | None:
    t = raw.replace("−", "-").replace("₹", "").replace(",", "").strip()
    m = re.match(r"([-+]?\d+(?:\.\d+)?)\s?(%|σ|bps|bp|cr|crore|lakh|x)?$", t)
    if not m:
        return None
    v = float(m.group(1))
    unit = m.group(2) or ""
    if unit == "%":
        return [v, v / 100]
    if unit in ("bps", "bp"):
        return [v, v / 10000, v / 100]
    if unit in ("cr", "crore"):
        return [v * 1e7, v]
    if unit == "lakh":
        return [v * 1e5, v]
    return [v]


def _matches(values: list[float], pool: list[float]) -> bool:
    for a in values:
        for b in pool:
            # 0.5 % relative tolerance; absolute 0.051 for numbers ≥ 1 (rounding), tighter for small fractions
            tol = max(0.005 * abs(b), 0.051 if abs(b) >= 1 else 0.0051)
            if abs(a - b) <= tol:
                return True
    return False


def _ignorable(raw: str, text: str, end: int, horizon: int, query_nums: set[str]) -> bool:
    core = raw.replace("−", "-").replace("₹", "").replace(",", "").strip()
    nxt = text[end:end + 1]
    if not raw[-1:].isspace() and nxt.isalpha() and not raw.rstrip().endswith(("%", "σ")):   # 5d, 3rd… labels
        return True
    if not raw[-1:].isspace() and nxt == "-" and text[end + 1:end + 2].isalpha():   # "10-year", "52-week" labels
        return True
    if re.fullmatch(r"(19|20)\d{2}", core):                              # years
        return True
    after = text[end:end + 8].lower()
    if core.lstrip("+-") == str(horizon) and re.match(r"\s?-?(day|trading day|d\b)", after):
        return True
    num = re.search(r"\d+(?:\.\d+)?", core)
    if num and num.group(0) in query_nums:
        return True
    return False


def validate(draft: str, evidence: list[dict], horizon_days: int = 5, query: str = "",
             signals: list[dict] | None = None, allow_fixture: bool = True) -> tuple[ValidatorReport, str]:
    """Return (report, answer_markdown). Flags (⚠️) or strips unsupported figures.

    Signal confidences shown to the synthesizer are traceable too: each counts for the signal's evidence ids.
    With allow_fixture=False (any non-MOCK run) fixture evidence is not an acceptable source: its numbers do not
    count as matched, and its ids are reported in rejected_evidence."""
    rejected = [] if allow_fixture else [e["id"] for e in evidence if e.get("fixture")]
    evidence = [e for e in evidence if e["id"] not in set(rejected)]
    by_id = {e["id"]: e for e in evidence}
    pools = {eid: pool_for([e]) for eid, e in by_id.items()}
    for sig in signals or []:
        c = sig.get("confidence")
        if isinstance(c, (int, float)):
            extra = pool_for([{"value": {"signal_confidence": c}}])
            for eid in sig.get("evidence_ids", []):
                if eid in pools:
                    pools[eid] = pools[eid] + extra
    global_pool = [v for pool in pools.values() for v in pool]
    query_nums = {m.group(0) for m in re.finditer(r"\d+(?:\.\d+)?", query)}
    text = JSON_TAIL.sub("", draft or "").rstrip()
    lines = text.split("\n")
    sentences: list[list[str]] = []
    found: list[Found] = []
    matched = 0
    bad_unmatched: list[Found] = []
    bad_uncited: list[Found] = []
    auto: list[tuple[Found, str]] = []
    ambiguous: dict[int, list[str]] = {}          # id(Found) → candidate evidence ids

    prefixes: list[str] = []
    for li, line in enumerate(lines):
        mk = LIST_MARKER.match(line)
        prefix = mk.group(0) if mk else ""           # "2. " list markers are never figures
        prefixes.append(prefix)
        body = line[len(prefix):]
        sents = SENT_SPLIT.split(body) if body.strip() else [body]
        sentences.append(sents)
        if line.lstrip().startswith("#"):
            continue
        for si, sent in enumerate(sents):
            # a sentence without its own citation may borrow the one that closes its bullet/line (later sentences only)
            cites = CITE.findall(sent) or next((c for later in sents[si + 1:] if (c := CITE.findall(later))), [])
            # blank valid citations AND malformed ones ("[ev_macro_00-01]"): digits inside an ev_ reference are no figure
            scan = DATE.sub(lambda m: " " * len(m.group(0)), BAD_CITE.sub(lambda m: " " * len(m.group(0)), sent))
            cited_pool = [v for c in cites if c in pools for v in pools[c]]
            for m in NUM.finditer(scan):
                raw = m.group(0)
                if _ignorable(raw, scan, m.end(), horizon_days, query_nums):
                    continue
                vals = _normalise(raw)
                if vals is None:
                    continue
                f = Found(raw.strip(), vals, li, si)
                found.append(f)
                if cites and _matches(vals, cited_pool):
                    matched += 1
                elif _matches(vals, global_pool):
                    owners = [eid for eid, pool in pools.items() if _matches(vals, pool)]
                    if len(owners) == 1:           # unambiguous: insert the citation instead of flagging
                        auto.append((f, owners[0]))
                        matched += 1
                    else:
                        bad_uncited.append(f)      # exists in evidence, but which item is ambiguous
                        ambiguous[id(f)] = owners
                else:
                    bad_unmatched.append(f)

    # resolve ambiguous figures by sentence context: an evidence item already cited (or auto-cited) in the same
    # sentence wins, e.g. "VaR of ₹48,200 and expected shortfall of ₹63,900 [ev_risk_001]"
    sent_ids: dict[tuple[int, int], set[str]] = {}
    for f, eid in auto:
        sent_ids.setdefault((f.line_idx, f.sent_idx), set()).add(eid)
    for li, sents in enumerate(sentences):
        for si, sent in enumerate(sents):
            sent_ids.setdefault((li, si), set()).update(CITE.findall(sent))
    still = []
    for f in bad_uncited:
        owners = ambiguous.get(id(f), [])
        literal = [eid for eid in owners if f.raw in (by_id[eid].get("summary") or "")]
        if set(owners) & sent_ids.get((f.line_idx, f.sent_idx), set()):
            matched += 1
        elif len(literal) == 1:           # the exact figure ("₹6,397") is printed in one evidence summary: cite it
            auto.append((f, literal[0]))
            matched += 1
        else:
            still.append(f)
    bad_uncited = still

    n_bad = len(bad_unmatched) + len(bad_uncited)
    if n_bad == 0:
        action = "pass"
    elif len(bad_unmatched) > 2:
        action = "stripped"
    else:
        action = "flagged"

    drop = {(f.line_idx, f.sent_idx) for f in bad_unmatched} if action == "stripped" else set()
    for f, eid in auto:
        sents = sentences[f.line_idx]
        tag = f"[{eid}]"
        s = sents[f.sent_idx]
        i = s.find(f.raw)
        if i >= 0 and not s[i + len(f.raw):].lstrip().startswith(tag):
            sents[f.sent_idx] = s[:i + len(f.raw)] + f" {tag}" + s[i + len(f.raw):]
    flag = bad_uncited + (bad_unmatched if action == "flagged" else [])
    for f in flag:
        sents = sentences[f.line_idx]
        if f.raw in sents[f.sent_idx] and f"⚠️{f.raw}" not in sents[f.sent_idx]:
            sents[f.sent_idx] = sents[f.sent_idx].replace(f.raw, f"⚠️{f.raw}", 1)
    out_lines = []
    for li, sents in enumerate(sentences):
        kept = [s for si, s in enumerate(sents) if (li, si) not in drop]
        if sents and not kept:
            continue
        out_lines.append(prefixes[li] + (" ".join(kept) if len(sents) > 1 else (kept[0] if kept else "")))
    answer = "\n".join(out_lines)
    if action == "stripped":
        answer += f"\n\n_{len(drop)} sentence(s) with {len(bad_unmatched)} unsupported figures removed by the Numbers Ledger._"
    unmatched = [f.raw for f in bad_unmatched] + [f"uncited:{f.raw}" for f in bad_uncited]
    if rejected and action == "pass":
        action = "flagged"
    report = ValidatorReport(numbers_found=len(found), numbers_matched=matched, unmatched=unmatched, action=action,
                             auto_cited=[f"{f.raw}→{eid}" for f, eid in auto], rejected_evidence=rejected)
    return report, answer


async def validator(state: dict) -> dict:
    run_id = state["run_id"]
    await bus.emit(run_id, "validator", "started", message="checking every number against the evidence")
    t0 = time.perf_counter()
    report, answer = validate(state.get("draft", ""), state.get("evidence", []),
                              int(state.get("intent", {}).get("horizon_days", 5)), state["request"]["query"],
                              signals=state.get("signals", []), allow_fixture=get_settings().MOCK)
    msg = f"Numbers Ledger {report.numbers_matched}/{report.numbers_found} · {report.action}"
    if report.rejected_evidence:
        msg += f" · {len(report.rejected_evidence)} fixture item(s) rejected"
    if report.auto_cited:
        msg += f" · {len(report.auto_cited)} citation(s) added"
    ms = int((time.perf_counter() - t0) * 1000)
    await bus.emit(run_id, "validator", "finished" if report.action == "pass" else "degraded", message=msg,
                   latency_ms=ms, meta={"unmatched": report.unmatched, "auto_cited": report.auto_cited})
    return {"validator": report.model_dump(mode="json"), "answer_markdown": answer, "latency": {"validator": ms}}
