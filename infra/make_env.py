"""Regenerate infra/env/{L1,L2,L3,single}.env from .env.example so every file has identical keys (02 §7 step 1).

    python infra/make_env.py                                   # sample hotspot IPs 192.168.43.101-103
    python infra/make_env.py --hosts 192.168.43.12 192.168.43.57 192.168.43.88
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTES = {
    "L1": "L1 'Brain': orchestrator :8000, Ollama qwen3:4b-instruct",
    "L2": "L2 'Quant & ML': quant/sentiment/agri/vectordb :8101-8104, Weaviate :8080, Ollama gemma3:4b",
    "L3": "L3 'Edge & UI': ingestion :8201, monitor :8202, Next.js :3000, Ollama qwen3:1.7b + phi4-mini",
    "single": "demo fallback: everything on one laptop (127.0.0.1) with MOCK=1",
}


def render(base: str, hosts: tuple[str, str, str], laptop: str, mock: int) -> str:
    s = base
    for i, h in enumerate(hosts, start=1):
        s = re.sub(rf"^L{i}_HOST=.*$", f"L{i}_HOST={h}", s, flags=re.M)
    s = re.sub(r"^MOCK=.*$", f"MOCK={mock}                 # 1 = fixtures + canned LLM output, nothing else needs to run",
               s, flags=re.M)
    s = re.sub(r"^# Copy to \.env.*\n", "", s, flags=re.M)
    name = "single.env" if laptop == "single" else f"{laptop}.env"
    header = (f"# infra/env/{name}: {NOTES[laptop]}\n"
              f"# Activate on this laptop:  powershell -ExecutionPolicy Bypass -File infra/use_env.ps1 -Laptop {laptop}\n"
              "# If the hotspot hands out new IPs, edit ONLY the three *_HOST lines; every URL derives from them.\n"
              f"THIS_LAPTOP={laptop}\n")
    return header + s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hosts", nargs=3, metavar=("L1", "L2", "L3"), default=["192.168.43.101", "192.168.43.102", "192.168.43.103"])
    a = ap.parse_args()
    base = (ROOT / ".env.example").read_text(encoding="utf-8")
    out = ROOT / "infra" / "env"
    out.mkdir(parents=True, exist_ok=True)
    for laptop in ("L1", "L2", "L3"):
        (out / f"{laptop}.env").write_text(render(base, tuple(a.hosts), laptop, 0), encoding="utf-8")
    (out / "single.env").write_text(render(base, ("127.0.0.1",) * 3, "single", 1), encoding="utf-8")
    print(f"wrote L1.env L2.env L3.env single.env to {out} (hosts {', '.join(a.hosts)})")


if __name__ == "__main__":
    main()
