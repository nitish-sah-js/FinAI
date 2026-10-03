"""Structured prediction extraction (spec A2). Never parses prose. Shapes per 01_CONTRACTS §5-6."""
from __future__ import annotations

import re

CONF_MAP = {"low": 0.55, "medium": 0.65, "high": 0.8}
DIR_EPS = 0.002
_PCT = re.compile(r"[-+−]?\d+(?:\.\d+)?(?=\s*%)")


def _evidence_list(final: dict, events: list[dict] | None = None) -> list[dict]:
    """Evidence lives in FinalAnswer.evidence (contract §5). `events` (AgentEvents) carry no evidence payloads."""
    return [e for e in (final.get("evidence") or []) if isinstance(e, dict)]


def parse_range(rng) -> tuple[float, float] | None:
    """holdings_impact.range: the orchestrator writes a STRING ("-4.1% to +1.2% (5d)"); a [lo, hi] list is accepted too."""
    if isinstance(rng, (list, tuple)) and len(rng) == 2 and all(isinstance(x, (int, float)) for x in rng):
        return float(rng[0]), float(rng[1])
    if isinstance(rng, str):
        nums = [float(x.replace("−", "-")) / 100 for x in _PCT.findall(rng)]
        if len(nums) >= 2:
            return min(nums[:2]), max(nums[:2])
    return None


def extract_prediction(final: dict, asset: str, horizon: str = "5d", events: list[dict] | None = None) -> dict | None:
    """Return dict(median,p10,p90,conformal_lo,conformal_hi,direction,confidence,source) or None."""
    entry = None
    for e in _evidence_list(final):
        if e.get("tool") != "analogs":
            continue
        dist = (e.get("value") or {}).get("distribution")
        if isinstance(dist, list):
            entry = next((d for d in dist if d.get("asset") == asset and str(d.get("horizon")) == horizon), None)
        if entry:
            break

    median = p10 = p90 = clo = chi = None
    source = None
    if entry is not None:
        median, p10, p90 = entry.get("median"), entry.get("p10"), entry.get("p90")
        clo, chi = entry.get("conformal_lo"), entry.get("conformal_hi")
        source = "analogs.distribution"
    else:  # holdings_impact: {"ticker","impact","range","evidence_ids"}
        for h in final.get("holdings_impact") or []:
            if h.get("ticker") == asset:
                rng = parse_range(h.get("range"))
                if rng:
                    p10, p90 = rng
                    median = (rng[0] + rng[1]) / 2
                    source = "holdings_impact.range"
                break
    if median is None:
        return None
    direction = 0 if abs(median) < DIR_EPS else (1 if median > 0 else -1)
    conf = CONF_MAP.get(str(final.get("confidence", "medium")).lower(), 0.65)
    return {"median": median, "p10": p10, "p90": p90, "conformal_lo": clo, "conformal_hi": chi,
            "direction": direction, "confidence": conf, "source": source}


def similarity_of_top_analog(final: dict, events: list[dict] | None = None) -> float | None:
    for e in _evidence_list(final):
        if e.get("tool") == "analogs":
            sims = [a.get("similarity") for a in (e.get("value") or {}).get("analogs", [])
                    if isinstance(a, dict) and a.get("similarity") is not None]
            if sims:
                return max(sims)
    return None


def holdout_analogs(final: dict, holdout_ids: set[str]) -> list[str]:
    """Held-out events the analog search returned anyway: any hit is leakage (12 §A3.2)."""
    out = []
    for e in _evidence_list(final):
        if e.get("tool") == "analogs":
            out += [a.get("event_id") for a in (e.get("value") or {}).get("analogs", [])
                    if isinstance(a, dict) and a.get("event_id") in holdout_ids]
    return out


def check_leakage(final: dict, events: list[dict] | None, as_of: str) -> list[str]:
    """Violations: Evidence.as_of (datetime) later than the case as_of date."""
    return [f"{e.get('id')} ({e.get('tool')}): as_of {e['as_of']} > {as_of}"
            for e in _evidence_list(final) if e.get("as_of") and str(e["as_of"])[:10] > as_of[:10]]
