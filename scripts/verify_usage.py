"""Prove that every service and every LLM role is really used (Phase 5). Runs a fixed set of questions through the
running orchestrator, then checks the per-run trace (GET /runs/{id}/trace) and each service's LLM call log
(GET /llm/calls) for:

  * every service called with a usable answer: quant, sentiment, agri, vectordb, ingestion, monitor
  * every model served by its OWN role at least once: qwen3:4b-instruct, gemma3:4b, qwen3:1.7b, phi4-mini
    (a call that fell back to another model counts as a fallback, not as a hit for the missing model)
  * no fixture evidence in any answer, and the validator passed every analytic answer

    .venv/Scripts/python scripts/verify_usage.py              # LLM cache reads off for these runs (real calls)
    .venv/Scripts/python scripts/verify_usage.py --use-cache  # allow recorded answers (faster; marked "cached")

Exit code 0 only when everything was hit. Otherwise prints what was missed and exits 1. Needs MOCK=0.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "copilot_common"))
sys.path.insert(0, str(ROOT / "packages" / "copilot_llm"))
from copilot_common.settings import get_settings  # noqa: E402

SERVICES = ["quant", "sentiment", "agri", "vectordb", "ingestion", "monitor"]
# (label, question, follow-up of label or None, kind) ; kind "analysis" must pass the validator
QUESTIONS = [
    ("cyclone+monsoon FMCG", "A severe cyclone hits Odisha during a weak monsoon. What happens to my FMCG holdings?", None, "analysis"),
    ("crude shock", "Crude oil jumps 15% after a surprise OPEC cut. What is the impact on my portfolio?", None, "analysis"),
    ("RBI rate", "How would a 50 bps RBI repo rate hike affect my portfolio?", None, "analysis"),
    ("rank exposure", "Rank my holdings by exposure to crude oil", None, "analysis"),
    ("what-if", "What if crude rises 10% and the repo rate goes up 25 bps?", None, "analysis"),
    ("greeting", "hi", None, "conversation"),
    ("unclear", "asdfgh qwerty", None, "conversation"),
    ("explain follow-up", "why?", "crude shock", "explain"),
]


def _client(s) -> httpx.Client:
    headers = {"X-Cluster-Key": s.CLUSTER_KEY} if s.CLUSTER_KEY else {}
    return httpx.Client(timeout=30, headers=headers)


def run(c: httpx.Client, orch: str, query: str, ref: str | None, no_cache: bool, timeout_s: float) -> tuple[str, dict]:
    body = {"query": query, "no_cache": no_cache}
    if ref:
        body["ref_run_id"] = ref
    run_id = c.post(f"{orch}/query", json=body).raise_for_status().json()["run_id"]
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = c.get(f"{orch}/runs/{run_id}")
        if r.status_code == 200 and r.json().get("final") and r.json().get("status") in ("done", "failed"):
            return run_id, r.json()["final"]
        time.sleep(1.0)
    raise TimeoutError(f"{query!r} did not finish in {timeout_s:.0f}s (run {run_id})")


def own_model(role: str) -> str | None:
    """The model a role is meant to run on (first local provider of its chain, copilot_llm.routing)."""
    from copilot_llm.providers import build_providers
    from copilot_llm.routing import ROLE_TABLE
    spec = ROLE_TABLE.get(role)
    return build_providers()[spec.local_chain[0]].model if spec else None


def table(rows: list[list[str]], head: list[str]) -> str:
    w = [max(len(str(x)) for x in col) for col in zip(head, *rows)]
    line = lambda r: "  ".join(str(x).ljust(n) for x, n in zip(r, w))  # noqa: E731
    return "\n".join([line(head), line(["-" * n for n in w]), *map(line, rows)])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--use-cache", action="store_true", help="allow recorded LLM answers (marked cached)")
    ap.add_argument("--timeout", type=float, default=240)
    a = ap.parse_args(argv)
    s = get_settings()
    if s.MOCK:
        print("MOCK=1: fixtures and canned LLM output, nothing real to verify. Set MOCK=0 and restart.")
        return 2
    orch = s.ORCH_URL.rstrip("/")
    expected_models = {s.OLLAMA_MODEL_L1: "intent / synthesizer / explain (L1)",
                       s.OLLAMA_MODEL_L2: "narrators, sentiment 2nd opinion (L2)",
                       s.OLLAMA_MODEL_L3_FAST: "alert text (L3, monitor)",
                       s.OLLAMA_MODEL_L3_RED: "red team (L3)"}
    since = datetime.now(timezone.utc).isoformat()
    c = _client(s)
    runs: dict[str, tuple[str, dict]] = {}
    problems: list[str] = []
    print(f"running {len(QUESTIONS)} questions against {orch} (LLM cache {'allowed' if a.use_cache else 'off'})")
    for label, q, ref_label, kind in QUESTIONS:
        ref = runs[ref_label][0] if ref_label and ref_label in runs else None
        t0 = time.perf_counter()
        try:
            runs[label] = run(c, orch, q, ref, not a.use_cache, a.timeout)
        except (httpx.HTTPError, TimeoutError) as e:
            problems.append(f"{label}: run failed ({e})")
            print(f"  {label:22} FAILED {e}")
            continue
        final = runs[label][1]
        print(f"  {label:22} {runs[label][0]}  {time.perf_counter() - t0:5.1f}s  intent={final['intent']['intent']}"
              f"  kind={final.get('kind', 'analysis')}  validator={final['validator']['action']}")
        if kind == "analysis" and final["validator"]["action"] != "pass":
            problems.append(f"{label}: validator {final['validator']['action']} "
                            f"(unmatched {final['validator']['unmatched'][:3]})")
        fixtures = [e["id"] for e in final.get("evidence", []) if e.get("fixture")]
        if fixtures:
            problems.append(f"{label}: fixture evidence used {fixtures}")

    trace: list[dict] = []
    for label, (run_id, _) in runs.items():
        for row in c.get(f"{orch}/runs/{run_id}/trace").json():
            trace.append({**row, "question": label})
    # LLM calls made inside other services (sentiment 2nd opinion on L2, monitor alert text on L3)
    llm_rows = [r for r in trace if r["service"].startswith("llm:")]
    remote: list[dict] = []
    for name, attr in (("sentiment", "SENTIMENT_URL"), ("monitor", "MONITOR_URL")):
        try:
            for r in c.get(f"{getattr(s, attr).rstrip('/')}/llm/calls", params={"since": since}).json():
                remote.append({**r, "service": f"llm:{r['role']}", "node": name, "question": "(in service)"})
        except (httpx.HTTPError, ValueError) as e:
            problems.append(f"{name}: /llm/calls unreadable ({type(e).__name__})")

    # ---- services
    svc_rows = []
    for svc in SERVICES:
        rows = [r for r in trace if r["service"] == svc]
        good = [r for r in rows if r["status"] in ("ok", "degraded")]
        bad = sorted({f"{r['status']}: {r['detail']}" for r in rows if r["status"] not in ("ok", "degraded")})
        svc_rows.append([svc, len(rows), len(good), sorted({r["node"] for r in good}) or "-", "HIT" if good else "NOT HIT",
                         "; ".join(bad)[:90]])
        if not good:
            problems.append(f"service {svc} never answered ({'; '.join(bad)[:120] or 'never called'})")
        if any(r["status"] == "fixture" for r in rows):
            problems.append(f"service {svc}: fixture data in trace")
    print("\nSERVICES\n" + table(svc_rows, ["service", "calls", "usable", "called by", "result", "problems"]))

    # ---- models
    all_llm = llm_rows + remote
    mrows = []
    for model, role in expected_models.items():
        own = [r for r in all_llm if r.get("model") == model and r["status"] == "ok"]
        cached = [r for r in all_llm if r.get("model") == model and r["status"] == "cached"]
        fell = [r for r in all_llm if r["status"] == "fallback" and own_model(r["service"][4:]) == model]
        mrows.append([model, role, len(own), len(cached), len(fell), "HIT" if own else "NOT HIT"])
        if not own:
            why = f"{len(fell)} calls fell back to another model" if fell else "never asked"
            problems.append(f"model {model} ({role}) never served its role: {why}")
    print("\nMODELS\n" + table(mrows, ["model", "role", "own calls", "cached", "fell back", "result"]))
    fb = [r for r in all_llm if r["status"] == "fallback"]
    if fb:
        print("\nFALLBACKS (role answered by another model)")
        print(table([[r["question"], r["node"], r["service"], r.get("model") or "-", (r.get("detail") or
                      "; ".join(r.get("fallbacks", [])))[:80]] for r in fb], ["question", "node", "role", "served by", "why"]))
    failed = [r for r in all_llm if r["status"] == "failed"]
    if failed:
        problems.append(f"{len(failed)} LLM calls got no answer from any model")

    print()
    if problems:
        print("FAIL: not everything was used")
        for p in problems:
            print("  - " + p)
        return 1
    print("PASS: every service and every model role was used, no fixture evidence, validator passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
