"""Statistics: weighted quantiles, similarity weights, confidence rule (08 §6.8), split-conformal q̂ (08 §6.7)."""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import numpy as np

from .paths import conformal_path

logger = logging.getLogger(__name__)

COVERAGE = 0.8
MIN_CALIB = 8                       # below this, warn "conformal interval unreliable (n<8)"


def weighted_quantile(values: list[float], weights: list[float], q: float) -> float:
    """Weighted quantile; each sorted value sits at the midpoint of its weight mass, linear in between."""
    if not values:
        return float("nan")
    arr = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    if w.sum() <= 0:
        w = np.ones_like(arr)
    w = w / w.sum()
    idx = np.argsort(arr)
    arr, w = arr[idx], w[idx]
    mid = np.cumsum(w) - w / 2
    return float(np.interp(q, mid, arr))


def similarity_weights(similarities: list[float]) -> list[float]:
    """w_i = sim_i² / Σ sim² (08 §6.6)."""
    if not similarities:
        return []
    sq = np.asarray(similarities, dtype=float) ** 2
    total = sq.sum()
    if total == 0:
        return [1.0 / len(similarities)] * len(similarities)
    return (sq / total).tolist()


def confidence_label(n: int, mean_sim: float, filters_relaxed: int = 0) -> tuple[str, float]:
    """(label, Evidence.confidence): high 0.8 / medium 0.6 / low 0.3, minus 0.1 per relaxed filter."""
    if n >= 5 and mean_sim >= 0.75:
        label, conf = "high", 0.8
    elif n >= 3 and mean_sim >= 0.6:
        label, conf = "medium", 0.6
    else:
        label, conf = "low", 0.3
    return label, round(max(0.0, conf - 0.1 * filters_relaxed), 3)


def conformal_q_from_residuals(residuals: list[float], coverage: float = COVERAGE) -> float | None:
    """q̂ = quantile(residuals, ceil((n+1)·coverage)/n), conservative ("higher") order statistic."""
    n = len(residuals)
    if n == 0:
        return None
    level = min(1.0, math.ceil((n + 1) * coverage) / n)
    return float(np.quantile(np.asarray(residuals, dtype=float), level, method="higher"))


# ---------- conformal table (cached, invalidated on file mtime) ----------
_cache: dict = {"path": None, "mtime": None, "data": {}}


def load_conformal(path: Path | None = None) -> dict:
    p = Path(path) if path else conformal_path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {}
    if _cache["path"] != str(p) or _cache["mtime"] != mtime:
        try:
            _cache.update(path=str(p), mtime=mtime, data=json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            logger.warning("cannot read %s: %s", p, e)
            return {}
    return _cache["data"]


def get_conformal_q(horizon: str, event_type: str | None, measure: str | None = None) -> tuple[float | None, int, str | None]:
    """(q̂, n_calib, source key). Preference: type+measure (if n ≥ 8) → type → _all+measure (if n ≥ 8) → _all."""
    h = load_conformal().get(horizon) or {}
    cands: list[tuple[str, dict | None, bool]] = []
    if event_type and event_type in h:
        t = h[event_type]
        cands += [(f"{event_type}/{measure}", (t.get("by_measure") or {}).get(measure or ""), True), (event_type, t, False)]
    if "_all" in h:
        a = h["_all"]
        cands += [(f"_all/{measure}", (a.get("by_measure") or {}).get(measure or ""), True), ("_all", a, False)]
    for key, entry, needs_n in cands:
        if not entry or entry.get("q") is None:
            continue
        n = int(entry.get("n_calib", 0))
        if needs_n and n < MIN_CALIB:
            continue
        return float(entry["q"]), n, key
    return None, 0, None
