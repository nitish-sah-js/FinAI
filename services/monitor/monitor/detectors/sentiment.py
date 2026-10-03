from __future__ import annotations

from . import Candidate


def detect_sentiment_shift(sentiments: list[dict], thresholds: dict) -> list[Candidate]:
    """sentiments: [{ticker, mean_6h, mean_24h, n_6h, conf}] → |Δ| ≥ shift and n_6h ≥ min_n."""
    out = []
    thr, min_n = thresholds.get("sentiment_shift", 0.4), thresholds.get("sentiment_min_n", 3)
    for s in sentiments:
        if s.get("mean_24h") is None or s.get("mean_6h") is None:
            continue
        delta = s["mean_6h"] - s["mean_24h"]
        if abs(delta) >= thr - 1e-9 and s["n_6h"] >= min_n:
            out.append(Candidate(
                kind="sentiment_shift", tickers=[s["ticker"]], key=s["ticker"],
                severity=round(min(abs(delta), 1.0), 4), relevance=1.0, confidence=float(s.get("conf") or 0.5),
                facts={"delta": round(delta, 2), "mean_6h": round(s["mean_6h"], 2),
                       "mean_24h": round(s["mean_24h"], 2), "n_6h": s["n_6h"]}))
    return out
