"""Prompt-eval harness (05 §4). Scores P1, P4–P9 on 10 golden cases with frozen evidence, prints a table,
saves evals/results/<timestamp>.json and exits 1 on a regression vs the previous best.

    python evals/run_evals.py                    # all prompts, local models, CACHE_MODE=off
    python evals/run_evals.py --prompt P4        # one prompt
    python evals/run_evals.py --boost            # cloud chain for synthesizer/planner (spends Groq quota)
    python evals/run_evals.py --repeat 3         # stability: every case 3×
    python evals/run_evals.py --cases 1,2,3      # subset

The production code is reused on purpose (validator, synthesizer inputs, section and JSON-tail parsing), so an eval
measures exactly what the orchestrator would do with that model output.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "services"))

PROMPTS = ["P1", "P4", "P5", "P6", "P7", "P8", "P9"]
NARRATOR = {"P4": ("weather_agent", ["weather", "exposure"]), "P5": ("agri_agent", ["agri"]),
            "P6": ("macro_agent", ["macro"]), "P7": ("analog_agent", ["analogs"])}
SECTIONS = {"en": ["Bottom line", "Impact on your holdings", "Suggested hedges", "Confidence and what could be wrong"],
            "hi": ["Saar", "Aapke holdings par asar", "Hedge sujhav", "Bharosa aur jokhim"]}
SLOW_MS = 5000          # 05 §7: each local prompt should run in under 5 s
_SENT = re.compile(r"[.!?](?:\s|$)")
_CITE = re.compile(r"\[(ev_[a-z_]+_\d{3})\]")


def configure(args) -> None:
    os.environ["CACHE_MODE"] = "record" if args.cache else "off"     # real results unless asked otherwise
    os.environ["LLM_MODE"] = "boost" if args.boost else os.environ.get("EVAL_LLM_MODE", "local")
    from copilot_common.settings import reload_settings
    reload_settings()


def load_cases(only: set[int] | None) -> list[dict]:
    cases = []
    for line in (HERE / "golden.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        if only and c["id"] not in only:
            continue
        c["bundle"] = json.loads((HERE / "fixtures" / f"case_{c['id']:02d}.json").read_text(encoding="utf-8"))
        cases.append(c)
    return cases


def n_sentences(text: str) -> int:
    return len([s for s in _SENT.split(_CITE.sub("", text or "")) if s.strip()])


# ---------------- scorers ----------------
def score_intent(parsed, expect: dict) -> tuple[float, list[str]]:
    """Field-level accuracy against the expected values; returns (score 0..1, failed check names)."""
    if parsed is None:
        return 0.0, ["invalid"]
    p = parsed if isinstance(parsed, dict) else parsed.model_dump()
    checks: list[tuple[str, bool]] = []
    for k, v in expect.items():
        if k == "region_contains":
            checks.append((k, v.lower() in (p.get("region") or "").lower()))
        elif k == "tools_include":
            checks.append((k, set(v) <= set(p.get("needs_tools") or [])))
        elif k == "tickers_include":
            checks.append((k, set(v) <= set(p.get("tickers") or [])))
        else:
            checks.append((k, p.get(k) == v))
    failed = [k for k, ok in checks if not ok]
    return (sum(ok for _, ok in checks) / len(checks) if checks else 1.0), failed


def grounding(text: str, evidence: list[dict], horizon: int, query: str, signals=None) -> dict:
    from orchestrator.nodes.validator import validate
    rep, _ = validate(text, evidence, horizon, query, signals=signals)
    found = rep.numbers_found
    by_model = found - len(rep.auto_cited) - len(rep.unmatched)
    return {"action": rep.action, "found": found, "unmatched": rep.unmatched, "auto_cited": rep.auto_cited,
            "cited": round(by_model / found, 3) if found else 1.0}


def hedge_unchanged(md: str, evidence: list[dict], lang: str) -> bool:
    """P8 must copy the computed hedge exactly: same instrument, same quantity, no other sized position."""
    head = SECTIONS["hi" if lang != "en" else "en"][2]
    m = re.search(rf"###\s*{re.escape(head)}[^\n]*\n(.*?)(?=\n###|\Z)", md, re.S | re.I)
    section = m.group(1) if m else ""
    props = next((e["value"]["proposals"] for e in evidence if e["tool"] == "hedge"), [])
    for p in props:
        if p["instrument"].lower() not in section.lower():
            return False
        qtys = {float(q) for q in re.findall(r"(\d+(?:\.\d+)?)\s*lots?\b", section, re.I)}
        if qtys and qtys != {float(p["quantity"])}:
            return False
    return bool(props)


# ---------------- prompt runners ----------------
async def run_p1(case, state):
    from copilot_llm import llm
    from orchestrator.nodes.parse_intent import IntentLLM, normalise
    from orchestrator.prompt_loader import render
    res = await llm.chat("intent", [{"role": "system", "content": "You convert market questions into strict JSON."},
                                    {"role": "user", "content": render("P1", query=case["query"])}],
                         schema=IntentLLM, max_tokens=300, timeout_s=120)
    parsed = normalise(res.parsed, case["query"]) if res.ok and res.parsed else None      # same path as production
    score, failed = score_intent(parsed, case["expect"])
    raw_score, _ = score_intent(res.parsed if res.ok else None, case["expect"])           # the model alone
    return res, {"valid": parsed is not None, "fields": score, "fields_raw": raw_score, "failed": failed,
                 "parsed": parsed.model_dump() if parsed else None, "fallbacks": res.fallbacks}


def run_rules(case) -> dict:
    from orchestrator.nodes.parse_intent import keyword_intent
    score, failed = score_intent(keyword_intent(case["query"]), case["expect"])
    return {"valid": True, "fields": score, "failed": failed}


async def run_narrator(prompt, case, state):
    from copilot_common.models import AgentSignal
    from copilot_llm import llm
    from orchestrator.nodes.common import compact
    from orchestrator.prompt_loader import render
    agent, tools = NARRATOR[prompt]
    evs = [e for e in case["bundle"]["evidence"] if e["tool"] in tools]
    text = render(prompt, evidence_json=[compact(e) for e in evs],
                  exposed_holdings=[{"ticker": h["ticker"], "sector": h["sector"]} for h in case["bundle"]["portfolio"]["holdings"]])
    res = await llm.chat("narrator", [{"role": "user", "content": text}], schema=AgentSignal, max_tokens=300,
                         timeout_s=120, fixture=f"narrator_{agent}")
    if not (res.ok and res.parsed):
        return res, {"valid": False, "grounded": False, "cited": 0.0, "length_ok": False}
    summary = res.parsed.summary
    g = grounding(summary, evs, 5, case["query"])
    return res, {"valid": True, "grounded": g["action"] == "pass", "cited": g["cited"], "length_ok": n_sentences(summary) <= 3,
                 "sentences": n_sentences(summary), "unmatched": g["unmatched"], "summary": summary}


def _signals(bundle) -> list[dict]:
    from copilot_common.models import Evidence
    from orchestrator.nodes.agents import code_signal
    evs = [Evidence.model_validate(e) for e in bundle["evidence"]]
    out = []
    for agent, tool, signal in [("weather_agent", "weather", "mixed"), ("agri_agent", "agri", "mixed"),
                                ("macro_agent", "macro", "neutral"), ("analog_agent", "analogs", "mixed"),
                                ("exposure_agent", "exposure", "n/a"), ("sentiment_agent", "sentiment", "neutral")]:
        out.append(code_signal(agent, [e for e in evs if e.tool == tool], signal).model_dump(mode="json"))
    return out


async def run_p8(case, state):
    from copilot_llm import llm
    from orchestrator.nodes.synthesizer import LANG_NAME, _FIRST_SECTION, split_answer, synth_inputs
    from orchestrator.prompt_loader import render
    b, lang = case["bundle"], case.get("lang", "en")
    st = {"request": {"query": case["query"], "lang": lang}, "portfolio": b["portfolio"], "evidence": b["evidence"],
          "signals": _signals(b)}
    inputs = synth_inputs(st)
    prompt = render("P8", **inputs) if lang == "en" else render("P8_hi", lang_name=LANG_NAME.get(lang, "Hinglish"), **inputs)
    role = "narrator" if lang != "en" and os.environ.get("LLM_MODE") == "local" else "synthesizer"
    res = await llm.chat(role, [{"role": "user", "content": prompt}], max_tokens=900, timeout_s=180, fixture="synthesizer")
    m = _FIRST_SECTION.search(res.text or "") if res.ok else None
    md, tail = split_answer(res.text[m.start():]) if m else ("", None)
    heads = SECTIONS["en" if lang == "en" else "hi"]
    sections_ok = bool(md) and all(re.search(rf"###\s*{re.escape(h)}", md, re.I) for h in heads)
    horizon = next((e["value"]["horizon_days"] for e in b["evidence"] if e["tool"] == "risk"), 5)
    g = grounding(md, b["evidence"], horizon, case["query"], st["signals"]) if md else {"action": "-", "cited": 0.0, "unmatched": []}
    state["draft"] = md
    return res, {"valid": sections_ok, "grounded": g["action"] == "pass", "cited": g["cited"], "json_tail": tail is not None,
                 "hedge_unchanged": hedge_unchanged(md, b["evidence"], lang) if md else False, "lang": lang,
                 "unmatched": g.get("unmatched", []), "auto_cited": g.get("auto_cited", [])}


async def run_p9(case, state):
    from copilot_common.models import RedTeamReport
    from copilot_llm import llm
    from orchestrator.nodes.common import compact
    from orchestrator.nodes.synthesizer import code_answer
    from orchestrator.prompt_loader import render
    b = case["bundle"]
    draft = state.get("draft") or code_answer({"request": {"query": case["query"]}, "portfolio": b["portfolio"],
                                                "evidence": b["evidence"], "signals": _signals(b)})
    text = render("P9", draft=draft, evidence_json=[compact(e, with_value=False) for e in b["evidence"]])
    res = await llm.chat("red_team", [{"role": "user", "content": text}], schema=RedTeamReport, max_tokens=400, timeout_s=120)
    if not (res.ok and res.parsed):
        return res, {"valid": False, "grounded": False, "three_reasons": False}
    from orchestrator.nodes.red_team import clean_reasons
    ids = {e["id"] for e in b["evidence"]}
    mentioned = set().union(*(set(re.findall(r"ev_[a-z_]+_\d{3}", r)) for r in res.parsed.reasons))
    reasons = clean_reasons(res.parsed.reasons, ids)                                       # same as production
    cited = [set(_CITE.findall(r)) for r in reasons]
    return res, {"valid": True, "three_reasons": len(reasons) == 3, "invented_ids": sorted(mentioned - ids),
                 "grounded": all(c <= ids for c in cited) and sum(1 for c in cited if c) >= 2,
                 "verdict": res.parsed.verdict, "reasons": reasons}


# ---------------- aggregation ----------------
def summarise(rows: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["prompt"], r["model"])].append(r)
    out = []
    for (prompt, model), rs in sorted(groups.items(), key=lambda kv: (PROMPTS + ["P1-rules"]).index(kv[0][0])
                                       if kv[0][0] in PROMPTS + ["P1-rules"] else 99):
        n = len(rs)
        ms = [r["ms"] for r in rs if r.get("ms") is not None]

        def rate(key):
            vals = [r[key] for r in rs if key in r]
            return round(sum(1 for v in vals if v) / len(vals), 3) if vals else None

        def mean(key):
            vals = [r[key] for r in rs if isinstance(r.get(key), (int, float)) and not isinstance(r.get(key), bool)]
            return round(statistics.mean(vals), 3) if vals else None

        extra = {}
        if prompt == "P1":
            extra = {"llm_only_fields": mean("fields_raw")}
        elif prompt == "P8":
            extra = {"hedge_unchanged": rate("hedge_unchanged"), "json_tail": rate("json_tail")}
        elif prompt == "P9":
            extra = {"three_reasons": rate("three_reasons")}
        elif prompt in NARRATOR:
            extra = {"length_ok": rate("length_ok")}
        out.append({"prompt": prompt, "model": model, "cases": n, "valid": rate("valid"), "grounded": rate("grounded"),
                    "fields": mean("fields"), "cited": mean("cited"), **extra,
                    "avg_ms": int(statistics.mean(ms)) if ms else None, "max_ms": max(ms) if ms else None})
    return out


def fmt(x, n=None):
    if x is None:
        return "-"
    if n is not None and isinstance(x, float) and x <= 1:
        return f"{round(x * n)}/{n}"
    return f"{x:.2f}" if isinstance(x, float) else str(x)


def print_table(summary: list[dict]) -> None:
    print(f"\n{'PROMPT':<9}{'MODEL':<20}{'CASES':>6}{'VALID':>8}{'GROUNDED':>10}{'FIELDS':>8}{'CITED':>7}"
          f"{'AVG_MS':>8}{'MAX_MS':>8}  EXTRA")
    for s in summary:
        n = s["cases"]
        extra = " ".join(f"{k}={fmt(v, n)}" for k, v in s.items()
                         if k in ("hedge_unchanged", "json_tail", "three_reasons", "length_ok"))
        if s.get("llm_only_fields") is not None:
            extra += f"llm_only_fields={s['llm_only_fields']:.2f}"
        slow = " SLOW" if s["avg_ms"] and s["avg_ms"] > SLOW_MS and s["model"] not in ("rules", "mock") else ""
        print(f"{s['prompt']:<9}{s['model'][:19]:<20}{n:>6}{fmt(s['valid'], n):>8}{fmt(s['grounded'], n):>10}"
              f"{fmt(s['fields']):>8}{fmt(s['cited']):>7}{fmt(s['avg_ms']):>8}{fmt(s['max_ms']):>8}  {extra}{slow}")


def regressions(summary: list[dict], results_dir: Path, current_file: Path) -> list[str]:
    """Any metric more than 10 % below the best previous run for the same (prompt, model)."""
    best: dict[tuple[str, str, str], float] = {}
    for f in sorted(results_dir.glob("*.json")):
        if f == current_file:
            continue
        try:
            for s in json.loads(f.read_text(encoding="utf-8"))["summary"]:
                for k in ("valid", "grounded", "fields", "cited", "hedge_unchanged", "three_reasons", "length_ok"):
                    if isinstance(s.get(k), (int, float)):
                        key = (s["prompt"], s["model"], k)
                        best[key] = max(best.get(key, 0.0), s[k])
        except (ValueError, KeyError):
            continue
    out = []
    for s in summary:
        for k in ("valid", "grounded", "fields", "cited", "hedge_unchanged", "three_reasons", "length_ok"):
            b = best.get((s["prompt"], s["model"], k))
            if b is not None and isinstance(s.get(k), (int, float)) and s[k] < b * 0.9:
                out.append(f"{s['prompt']} {s['model']} {k}: {s[k]:.2f} < best {b:.2f}")
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prompt", choices=PROMPTS, help="run only this prompt")
    ap.add_argument("--boost", action="store_true", help="LLM_MODE=boost (cloud chain; spends quota)")
    ap.add_argument("--repeat", type=int, default=1, help="run every case N times (stability)")
    ap.add_argument("--cases", help="comma list of case ids")
    ap.add_argument("--cache", action="store_true", help="use the LLM response cache (default: off, real results)")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--no-compare", action="store_true", help="skip the regression check")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    configure(args)
    from copilot_common.settings import get_settings
    from copilot_llm import llm

    s = get_settings()
    prompts = [args.prompt] if args.prompt else PROMPTS
    cases = load_cases({int(x) for x in args.cases.split(",")} if args.cases else None)
    print(f"evals: {len(cases)} cases × {len(prompts)} prompts × {args.repeat} · LLM_MODE={s.LLM_MODE} "
          f"CACHE_MODE={s.CACHE_MODE} MOCK={int(s.MOCK)}")
    if not s.MOCK:
        warm = await llm.warmup()
        print("warm-up:", warm or "no local model reachable")

    rows: list[dict] = []
    p1_by_case: dict[int, list[str]] = defaultdict(list)
    t_start = time.perf_counter()
    for rep in range(args.repeat):
        for case in cases:
            state: dict = {}
            line = [f"case {case['id']:>2}{f' r{rep + 1}' if args.repeat > 1 else ''}"]
            if "P1" in prompts and rep == 0:
                rows.append({"prompt": "P1-rules", "model": "rules", "case": case["id"], "ms": 0, **run_rules(case)})
            for p in prompts:
                t0 = time.perf_counter()
                try:
                    if p == "P1":
                        res, r = await run_p1(case, state)
                    elif p in NARRATOR:
                        res, r = await run_narrator(p, case, state)
                    elif p == "P8":
                        res, r = await run_p8(case, state)
                    else:
                        res, r = await run_p9(case, state)
                    model = res.model or res.provider or "none"
                    ms = res.latency_ms if res.ok and not res.cached else int((time.perf_counter() - t0) * 1000)
                except Exception as e:  # noqa: BLE001  one broken case must not stop the run
                    model, ms, r = "error", int((time.perf_counter() - t0) * 1000), {"valid": False, "error": repr(e)}
                if p == "P1" and r.get("parsed"):
                    p1_by_case[case["id"]].append(json.dumps(r["parsed"], sort_keys=True))
                rows.append({"prompt": p, "model": model, "case": case["id"], "repeat": rep, "ms": ms, **r})
                ok = "✓" if r.get("valid") and r.get("grounded", True) and r.get("fields", 1) >= 1 else "✗"
                line.append(f"{p}{ok}")
            print(" ".join(line), flush=True)

    summary = summarise(rows)
    print_table(summary)
    if args.repeat > 1 and p1_by_case:
        stable = sum(1 for v in p1_by_case.values() if len(set(v)) == 1)
        print(f"\nP1 stability: {stable}/{len(p1_by_case)} cases gave identical JSON across {args.repeat} repeats")
    failed_p1 = [(r["case"], r["failed"]) for r in rows if r["prompt"] == "P1" and r.get("failed")]
    if failed_p1:
        print("P1 failed checks:", "; ".join(f"case {c}: {', '.join(f)}" for c, f in failed_p1))
    ungrounded = [(r["prompt"], r["case"], r.get("unmatched")) for r in rows
                  if r["prompt"] in ("P4", "P5", "P6", "P7", "P8") and r.get("valid") and not r.get("grounded")]
    if ungrounded:
        print("ungrounded:", "; ".join(f"{p} case {c} {u}" for p, c, u in ungrounded))
    print(f"\ntotal {time.perf_counter() - t_start:.0f} s")

    results_dir = HERE / "results"
    out_file = results_dir / f"{datetime.now():%Y%m%d_%H%M%S}.json"
    if not args.no_save:
        results_dir.mkdir(exist_ok=True)
        out_file.write_text(json.dumps({"timestamp": datetime.now().isoformat(timespec="seconds"),
                                        "llm_mode": s.LLM_MODE, "mock": s.MOCK, "repeat": args.repeat,
                                        "summary": summary, "rows": rows}, indent=1, ensure_ascii=False, default=str),
                            encoding="utf-8")
        print(f"saved {out_file.relative_to(ROOT)}")
    if args.no_compare or s.MOCK:
        return 0
    regs = regressions(summary, results_dir, out_file)
    if regs:
        print("REGRESSION:\n  " + "\n  ".join(regs))
        return 1
    print("no regression vs previous best")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
