"""Run one query from the terminal and watch the live events (no server needed).

    cd services
    python -m orchestrator.cli "Cyclone heading to Odisha — what happens to my portfolio this week?"
    python -m orchestrator.cli --mode boost --lang hinglish --as-of 2021-08-25 "..."
    python -m orchestrator.cli --chaos weather_down,force_rate_limit "..."
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx

from copilot_common import reachability
from copilot_common.models import ChaosFlags, QueryRequest
from copilot_common.settings import get_settings, project_root

from .events import bus
from .graph import run_graph
from .ledger import ledger

PROBES = {"ollama L1": "OLLAMA_L1", "ollama L2": "OLLAMA_L2", "ollama L3": "OLLAMA_L3", "ingestion": "INGEST_URL",
          "quant": "QUANT_URL", "sentiment": "SENTIMENT_URL", "agri": "AGRI_URL", "vectordb": "VECTOR_URL"}


async def banner() -> None:
    """Show the mode and probe every dependency in parallel (0.3 s connect timeout). Down hosts are remembered,
    so the run skips them instantly instead of waiting ~2 s per refused connection on Windows."""
    s = get_settings()
    env_file = project_root() / ".env"
    print(f"MOCK={int(s.MOCK)}  LLM_MODE={s.LLM_MODE}  CACHE_MODE={s.CACHE_MODE}  "
          f".env={'found' if env_file.is_file() else 'MISSING (using defaults)'}")
    if s.MOCK:
        print("mock mode: fixtures + canned LLM output, nothing else needs to run")
        print()
        return

    async def probe(name: str, attr: str):
        url = getattr(s, attr).rstrip("/")
        path = "/api/tags" if attr.startswith("OLLAMA") else "/health"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=0.3)) as c:
                await c.get(url + path)
            return name, url, True
        except httpx.HTTPError:
            reachability.mark_down(url)
            return name, url, False

    results = await asyncio.gather(*(probe(n, a) for n, a in PROBES.items()))
    seen: dict[str, bool] = {}
    for name, url, ok in results:
        if url in seen and name.startswith("ollama"):
            continue
        seen[url] = ok
        print(f"  {'up  ' if ok else 'DOWN'}  {name:<10} {url}")
    if not any(ok for n, _, ok in results if n.startswith("ollama")):
        print("  → no Ollama reachable: keyword intent parser, code narrators and a template answer will be used.")
    else:
        from copilot_llm import llm
        print("  warming up local models (first load after a restart takes ~40 s) ...", flush=True)
        for model, status in (await llm.warmup()).items():
            print(f"    {model}: {status}")
    if not any(ok for n, _, ok in results if not n.startswith("ollama")):
        print("  → no L2/L3 services reachable: every tool uses its fixture, marked degraded (service_unreachable).")
        print("    For a clean demo without them, set MOCK=1 in .env (copy .env.example → .env).")
    print()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--mode", choices=["local", "boost", "auto"])
    ap.add_argument("--lang", default="en", choices=["en", "hi", "hinglish"])
    ap.add_argument("--as-of")
    ap.add_argument("--chaos", default="", help="comma list: weather_down,force_rate_limit,agri_raster_missing,vector_down")
    ap.add_argument("--json", action="store_true", help="print the full FinalAnswer JSON")
    a = ap.parse_args()
    await banner()
    chaos = ChaosFlags(**{k: True for k in a.chaos.split(",") if k})
    req = QueryRequest(query=a.query, llm_mode=a.mode, lang=a.lang, as_of=a.as_of, chaos=chaos)

    async def printer():
        async for m in bus.subscribe_activity():
            if m["type"] == "event":
                d = m["data"]
                extra = f" [{d['model']}@{d['provider']}]" if d.get("model") else ""
                print(f"{d['seq']:>3} {d['node']:<16} {d['status']:<9} {str(d.get('latency_ms') or ''):>6}  "
                      f"{(d.get('message') or '')[:110]}{extra}")

    task = asyncio.create_task(printer())
    final = await run_graph(req)
    await asyncio.sleep(0.05)
    task.cancel()
    print("\n" + ("=" * 80))
    if a.json:
        print(json.dumps(final, indent=2, ensure_ascii=False))
    else:
        print(final["answer_markdown"])
        print(f"\nconfidence={final['confidence']} · validator={final['validator']} · usage={final['llm_usage']}")
        print(f"latency_ms={final['latency_ms']}")
    await ledger.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
        print("interrupted.")
        sys.exit(130)
