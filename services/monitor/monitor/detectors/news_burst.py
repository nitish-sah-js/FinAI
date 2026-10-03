from __future__ import annotations

import math

from . import Candidate


def detect_news_burst(news_stats: list[dict], thresholds: dict) -> list[Candidate]:
    """news_stats: [{ticker, k, lambda_h, tagged, news_ids}] → z = (k−λ)/√λ ≥ z_thr and k ≥ k_thr.
    relevance 1.0 when the ticker is tagged on the items, 0.5 when only a sector keyword matched."""
    out = []
    z_thr, k_thr = thresholds.get("news_burst_z", 3.0), thresholds.get("news_burst_k", 3)
    floor = thresholds.get("news_lambda_floor", 0.5)
    for n in news_stats:
        k, lam = n["k"], max(n.get("lambda_h", 0.0), floor)
        z = (k - lam) / math.sqrt(lam)
        if z >= z_thr - 1e-9 and k >= k_thr:
            out.append(Candidate(
                kind="news_burst", tickers=[n["ticker"]], key=n["ticker"],
                severity=round(min(z / 8.0, 1.0), 4), relevance=1.0 if n.get("tagged", True) else 0.5, confidence=0.6,
                facts={"k": k, "lambda": round(lam, 2), "z": round(z, 2),
                       "headlines": n.get("titles", [])[:3]},
                evidence_ids=n.get("evidence_ids", [])))
    return out
