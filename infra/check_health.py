"""Health of every service, Ollama host and Weaviate across the 3 laptops (02 §6).

    python infra/check_health.py                       # uses the repo-root .env
    python infra/check_health.py --env infra/env/L1.env
    python infra/check_health.py --watch               # refresh every 5 s
    python infra/check_health.py --json                # machine output (terminal health page, 13)
    python infra/check_health.py --require orchestrator,ollama@L1    # exit 1 only if these are down

Exit code 1 if any *required* target is down (default: every target). "degraded" never fails the exit code.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]

SERVICES = [("orchestrator", "ORCH_URL"), ("quant", "QUANT_URL"), ("sentiment", "SENTIMENT_URL"), ("agri", "AGRI_URL"),
            ("vectordb", "VECTOR_URL"), ("ingestion", "INGEST_URL"), ("monitor", "MONITOR_URL")]
OLLAMAS = [("ollama@L1", "OLLAMA_L1", ["OLLAMA_MODEL_L1"]), ("ollama@L2", "OLLAMA_L2", ["OLLAMA_MODEL_L2"]),
           ("ollama@L3", "OLLAMA_L3", ["OLLAMA_MODEL_L3_FAST", "OLLAMA_MODEL_L3_RED"])]
COLORS = {"ok": "\033[32m", "degraded": "\033[33m", "down": "\033[31m"}
RESET = "\033[0m"


def load_settings(env_file: str | None):
    """Load an explicit env file (overriding the process env) or fall back to the repo .env, then build Settings."""
    sys.path.insert(0, str(ROOT / "packages" / "copilot_common"))
    if env_file:
        from dotenv import dotenv_values
        for k, v in dotenv_values(env_file).items():
            if v is not None:
                os.environ[k] = v
    os.environ.setdefault("COPILOT_ROOT", str(ROOT))
    from copilot_common.settings import reload_settings
    return reload_settings()


def _host(url: str) -> str:
    return urlsplit(url).hostname or url


def _has_model(installed: list[str], want: str) -> bool:
    return any(m == want or m == f"{want}:latest" or (":" not in want and m.startswith(want + ":")) for m in installed)


async def check_service(c: httpx.AsyncClient, name: str, url: str) -> dict:
    row = {"name": name, "kind": "service", "url": url, "host": _host(url)}
    t0 = time.perf_counter()
    try:
        r = await c.get(url.rstrip("/") + "/health")
        row["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        h = r.json()
        if h.get("service") != name:
            # something else owns this port (e.g. another project's container publishing :8000)
            row.update(status="down", detail=f"port taken by another app (service={h.get('service')!r}); stop it or change the port")
            return row
        row.update(status=h.get("status", "degraded") if r.status_code == 200 else "degraded",
                   mock=h.get("mock"), models=h.get("models", []), deps=h.get("deps", {}))
        bad = {k: v for k, v in row["deps"].items() if v != "ok"}
        row["detail"] = "; ".join(f"{k}:{v}" for k, v in bad.items()) or ", ".join(row["models"])
    except (httpx.HTTPError, ValueError) as e:
        row.update(status="down", latency_ms=None, detail=type(e).__name__)
    return row


async def check_ollama(c: httpx.AsyncClient, name: str, url: str, wanted: list[str]) -> dict:
    row = {"name": name, "kind": "ollama", "url": url, "host": _host(url), "mock": None}
    t0 = time.perf_counter()
    try:
        r = await c.get(url.rstrip("/") + "/api/tags")
        r.raise_for_status()
        row["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        installed = [m["name"] for m in r.json().get("models", [])]
        missing = [w for w in wanted if not _has_model(installed, w)]
        row.update(models=installed, missing=missing, status="degraded" if missing else "ok",
                   detail=(f"missing: {', '.join(missing)} (ollama pull …) · " if missing else "") + ", ".join(wanted))
    except (httpx.HTTPError, ValueError) as e:
        row.update(status="down", latency_ms=None, detail=type(e).__name__)
    return row


async def check_weaviate(c: httpx.AsyncClient, host: str) -> dict:
    url = f"http://{host}:8080"
    row = {"name": "weaviate", "kind": "weaviate", "url": url, "host": host, "mock": None}
    t0 = time.perf_counter()
    try:
        r = await c.get(url + "/v1/.well-known/ready")
        row["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        row["status"] = "ok" if r.status_code == 200 else "degraded"
        try:
            meta = (await c.get(url + "/v1/meta")).json()
            row["detail"] = f"v{meta.get('version')}"
        except (httpx.HTTPError, ValueError):
            row["detail"] = ""
    except httpx.HTTPError as e:
        row.update(status="down", latency_ms=None, detail=type(e).__name__)
    return row


async def collect(s, timeout_s: float) -> list[dict]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=min(timeout_s, 1.5))) as c:
        jobs = [check_service(c, n, getattr(s, attr)) for n, attr in SERVICES]
        jobs += [check_ollama(c, n, getattr(s, attr), [getattr(s, m) for m in models]) for n, attr, models in OLLAMAS]
        jobs.append(check_weaviate(c, s.WEAVIATE_HOST))
        return list(await asyncio.gather(*jobs))


def print_table(rows: list[dict], color: bool) -> None:
    print(f"{'SERVICE':<14} {'HOST':<16} {'STATUS':<9} {'LAT':>6}  {'MOCK':<4}  MODELS/DEPS")
    for r in rows:
        st = r["status"]
        st_txt = f"{COLORS[st]}{st:<9}{RESET}" if color else f"{st:<9}"
        lat = f"{r['latency_ms']}ms" if r.get("latency_ms") is not None else "-"
        mock = "-" if r.get("mock") is None else ("yes" if r["mock"] else "no")
        print(f"{r['name']:<14} {r['host']:<16} {st_txt} {lat:>6}  {mock:<4}  {r.get('detail', '')}")
    n = {k: sum(1 for r in rows if r["status"] == k) for k in ("ok", "degraded", "down")}
    print(f"\n{n['ok']} ok · {n['degraded']} degraded · {n['down']} down")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", help="env file to use (default: repo-root .env)")
    ap.add_argument("--watch", action="store_true", help="refresh every --interval seconds")
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    ap.add_argument("--require", default="all", help="comma list of names that must not be down (default: all)")
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("--no-color", action="store_true")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    color = not a.no_color and not a.json and sys.stdout.isatty()
    if color and os.name == "nt":
        os.system("")                      # enable ANSI colours in the Windows console
    s = load_settings(a.env)

    while True:
        rows = await collect(s, a.timeout)
        required = {r["name"] for r in rows} if a.require == "all" else {x.strip() for x in a.require.split(",")}
        for r in rows:
            r["required"] = r["name"] in required
        failed = [r["name"] for r in rows if r["required"] and r["status"] == "down"]
        if a.json:
            print(json.dumps({"checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "env": a.env or ".env",
                              "ok": not failed, "down_required": failed, "targets": rows}, indent=2))
        else:
            if a.watch:
                print("\033[2J\033[H" if color else "\n" + "-" * 80, end="")
            print(f"env: {a.env or str(ROOT / '.env')} · {time.strftime('%H:%M:%S')}\n")
            print_table(rows, color)
            if failed:
                print(f"required targets down: {', '.join(failed)}")
        sys.stdout.flush()                     # visible immediately when piped / tailed
        if not a.watch:
            return 1 if failed else 0
        await asyncio.sleep(a.interval)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
