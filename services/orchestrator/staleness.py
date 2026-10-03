"""Staleness factor (04 §3): old data counts less."""
from __future__ import annotations

from copilot_common.models import Evidence

HALF_LIFE_S = {"prices": 86_400, "news": 43_200, "sentiment": 43_200, "weather": 21_600,
               "macro": 7 * 86_400, "agri": 16 * 86_400, "exposure": 86_400,
               "analogs": None, "event_study": None, "risk": 86_400, "hedge": 86_400, "scenario": None,
               "correlations": 86_400}


def staleness_factor(tool: str, freshness_s: int | None) -> float:
    hl = HALF_LIFE_S.get(tool)
    if hl is None or freshness_s is None:
        return 1.0
    return max(0.3, 0.5 ** (freshness_s / hl))


def apply_staleness(evidence: list[Evidence]) -> list[Evidence]:
    for ev in evidence:
        ev.staleness_factor = round(staleness_factor(ev.tool, ev.freshness_s), 3)
    return evidence


def effective_confidence(ev: dict) -> float | None:
    c = ev.get("confidence")
    if c is None:
        return None
    return c * (ev.get("staleness_factor") or 1.0)
