"""data/region_exposure.json -> linked_equities (09 §9). Judgment-based mapping, not a measured sensitivity.

File shape: {"regions": {region_id: {"crops": [...], "equities": [{ticker, link, sign, strength}]}}, "sector_defaults": {...}}
Read-only: this module never writes the file.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from copilot_common.settings import get_data_dir


def exposure_path() -> Path:
    return get_data_dir() / "region_exposure.json"


@lru_cache
def _load(path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        return {"regions": {}, "sector_defaults": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def load_exposure() -> dict:
    return _load(str(exposure_path()))


def linked_equities(region_id: str) -> list[dict]:
    """[{ticker, link, sign, strength}] for the region (empty list if unmapped)."""
    reg = load_exposure().get("regions", {}).get(region_id) or {}
    out = []
    for e in reg.get("equities", []):
        if not isinstance(e, dict) or not e.get("ticker"):
            continue
        out.append({"ticker": e["ticker"], "link": e.get("link"), "sign": e.get("sign"),
                    "strength": e.get("strength")})
    return out


def _norm(t: str) -> str:
    t = t.strip().upper()
    return t[:-3] if t.endswith(".NS") else t


def regions_for_tickers(tickers: list[str]) -> dict[str, list[str]]:
    """region_id -> the requested tickers linked to it (any region in the file, model-known or not)."""
    want = {_norm(t): t for t in tickers if t}
    out: dict[str, list[str]] = {}
    for rid, reg in load_exposure().get("regions", {}).items():
        hits = [want[_norm(e["ticker"])] for e in reg.get("equities", []) if _norm(e.get("ticker", "")) in want]
        if hits:
            out[rid] = hits
    return out
