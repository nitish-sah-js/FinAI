from __future__ import annotations

from . import Candidate

CALM = {"healthy", "watch"}


def detect_agri_stress(agri: list[dict], region_links: dict[str, list[dict]], severity: dict,
                       held: set[str] | None = None) -> list[Candidate]:
    """agri: [{region, stress_class, prev_class, confidence, evidence_id}] → fires only on a TRANSITION into
    stressed/severe FROM healthy/watch (11 §3). The first poll after startup (prev unknown) only records the class, so a
    restart never re-announces old stress. One candidate per region."""
    out = []
    for a in agri:
        sc, pc = a.get("stress_class"), a.get("prev_class")
        links = [l for l in region_links.get(a["region"], []) if held is None or l["ticker"] in held]
        if sc not in severity or pc not in CALM or not links:
            continue
        out.append(Candidate(
            kind="agri_stress", tickers=[l["ticker"] for l in links], key=a["region"],
            severity=severity[sc], relevance=max(l["strength"] for l in links),
            confidence=float(a.get("confidence") if a.get("confidence") is not None else 0.5),
            facts={"region": a["region"], "stress_class": sc, "prev_class": pc,
                   "links": {l["ticker"]: l["strength"] for l in links}},
            evidence_ids=[a["evidence_id"]] if a.get("evidence_id") else []))
    return out
