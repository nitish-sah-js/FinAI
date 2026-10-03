"""Reliability bins (12 §A6): stated confidence {0.55, 0.65, 0.8} vs observed hit rate, with n per bin."""
from __future__ import annotations

BINS = (0.55, 0.65, 0.8)          # low / medium / high (12 §A2)


def calibration_bins(confidence: list[float], pred_dir: list[int], realized: list[float]) -> list[dict]:
    out = []
    for b in BINS:
        pts = [(d, r) for c, d, r in zip(confidence, pred_dir, realized) if abs(c - b) < 1e-9 and d != 0]
        hits = sum(1 for d, r in pts if d == (1 if r > 0 else -1 if r < 0 else 0))
        out.append({"stated": b, "observed": round(hits / len(pts), 4) if pts else None, "n": len(pts)})
    return out
