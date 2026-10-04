"""Block until every Ollama model this laptop's services use has answered once, and pin it with keep_alive=-1.

Run by infra/run_all_local.ps1 before the services start taking queries, so the first question does not hit a
~40 s cold load. Models that are not pulled, or hosts that are down, are reported and skipped (not waited on).

    .venv/Scripts/python infra/warmup.py            # all hosts/models in .env
    .venv/Scripts/python infra/warmup.py --local    # only models on this machine's Ollama (127.0.0.1)
Exit code 0 when at least one model loaded, 1 otherwise.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "copilot_common"))
from copilot_common.settings import get_settings  # noqa: E402

KEEP_ALIVE = -1


def targets(local_only: bool) -> list[tuple[str, str, str]]:
    s = get_settings()
    rows = [("intent / synthesizer / explain", s.OLLAMA_L1, s.OLLAMA_MODEL_L1),
            ("narrators / sentiment 2nd opinion", s.OLLAMA_L2, s.OLLAMA_MODEL_L2),
            ("alerts / small talk", s.OLLAMA_L3, s.OLLAMA_MODEL_L3_FAST),
            ("red team", s.OLLAMA_L3, s.OLLAMA_MODEL_L3_RED)]
    seen, out = set(), []
    for role, host, model in rows:
        host = host.rstrip("/")
        if local_only and not any(h in host for h in ("127.0.0.1", "localhost")):
            continue
        if (host, model) not in seen:
            seen.add((host, model))
            out.append((role, host, model))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true", help="only this machine's Ollama")
    ap.add_argument("--timeout", type=float, default=240)
    args = ap.parse_args()
    loaded = 0
    print(f"{'MODEL':22} {'HOST':28} {'RESULT':28} ROLE")
    for role, host, model in targets(args.local):
        try:
            tags = httpx.get(f"{host}/api/tags", timeout=3).json().get("models", [])
            names = {m.get("name", "") for m in tags}
        except (httpx.HTTPError, ValueError):
            print(f"{model:22} {host:28} {'host down':28} {role}")
            continue
        if not any(n == model or n == f"{model}:latest" for n in names):
            print(f"{model:22} {host:28} {'not pulled (ollama pull)':28} {role}")
            continue
        t0 = time.perf_counter()
        try:
            r = httpx.post(f"{host}/api/generate", json={"model": model, "prompt": "", "keep_alive": KEEP_ALIVE},
                           timeout=args.timeout)
            r.raise_for_status()
            loaded += 1
            print(f"{model:22} {host:28} {'loaded in %.1f s' % (time.perf_counter() - t0):28} {role}")
        except httpx.HTTPError as e:
            print(f"{model:22} {host:28} {'failed: ' + type(e).__name__:28} {role}")
    return 0 if loaded else 1


if __name__ == "__main__":
    sys.exit(main())
