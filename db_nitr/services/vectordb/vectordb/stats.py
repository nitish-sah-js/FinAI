"""
vectordb/stats.py
Statistical helpers: weighted quantiles, similarity weights, confidence labels,
and conformal interval loading.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from typing import Literal

import numpy as np


# ---------- Core stats ----------

def weighted_quantile(values: list[float], weights: list[float], q: float) -> float:
    """
    Compute the weighted quantile of a sorted dataset.
    Uses the "interpolated" method consistent with numpy percentile.
    """
    if not values:
        return float("nan")
    arr = np.array(values, dtype=float)
    w = np.array(weights, dtype=float)
    w = w / w.sum()  # normalize

    idx = np.argsort(arr)
    arr = arr[idx]
    w = w[idx]

    cumw = np.cumsum(w)
    # Shift so the midpoint of each weight mass sits on the sorted value
    cumw_shifted = cumw - w / 2
    return float(np.interp(q, cumw_shifted, arr))


def similarity_weights(similarities: list[float]) -> list[float]:
    """w_i = sim_i² / Σ sim²"""
    sims = np.array(similarities, dtype=float)
    sq = sims ** 2
    total = sq.sum()
    if total == 0:
        return [1.0 / len(similarities)] * len(similarities)
    return (sq / total).tolist()


def confidence_label(
    n: int, mean_sim: float, filters_relaxed: int = 0
) -> tuple[str, float]:
    """
    Returns (label, numeric_confidence).
    Confidence drops 0.1 per filter relaxation.
    """
    if n >= 5 and mean_sim >= 0.75:
        label, conf = "high", 0.8
    elif n >= 3 and mean_sim >= 0.6:
        label, conf = "medium", 0.6
    else:
        label, conf = "low", 0.3
    conf = max(0.0, conf - 0.1 * filters_relaxed)
    return label, conf


# ---------- Conformal interval ----------

_conformal_cache: dict | None = None


def _load_conformal() -> dict:
    global _conformal_cache
    if _conformal_cache is not None:
        return _conformal_cache
    paths = [
        Path("data/conformal_q.json"),
        Path(__file__).parent.parent / "data" / "conformal_q.json",
    ]
    for p in paths:
        if p.exists():
            _conformal_cache = json.loads(p.read_text())
            return _conformal_cache
    # Return empty sentinel if file not yet generated
    _conformal_cache = {}
    return _conformal_cache


def get_conformal_q(
    horizon: Literal["1d", "5d", "20d"],
    event_type: str | None,
) -> tuple[float | None, int]:
    """
    Returns (q_hat, n_calib).
    Looks up [horizon][event_type], falls back to [horizon]["_all"], then None.
    """
    data = _load_conformal()
    if not data:
        return None, 0
    h = data.get(horizon, {})
    if event_type and event_type in h:
        entry = h[event_type]
        return entry.get("q"), entry.get("n_calib", 0)
    if "_all" in h:
        entry = h["_all"]
        return entry.get("q"), entry.get("n_calib", 0)
    return None, 0
