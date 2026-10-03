"""Backtest metrics (12 §A5). Small sample: always report n and a bootstrap CI, never significance."""
from __future__ import annotations

import numpy as np


def _sign(x: float) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


def hit_rate(pred_dir: list[int], realized: list[float]) -> tuple[float | None, int]:
    """Share of non-zero predictions whose direction matches sign(realized). Returns (rate, n)."""
    pairs = [(d, r) for d, r in zip(pred_dir, realized) if d != 0]
    if not pairs:
        return None, 0
    return sum(1 for d, r in pairs if d == _sign(r)) / len(pairs), len(pairs)


def mae(pred: list[float], realized: list[float]) -> float | None:
    pairs = [(p, r) for p, r in zip(pred, realized) if p is not None]
    return float(np.mean([abs(p - r) for p, r in pairs])) if pairs else None


def coverage_80(p10: list[float | None], p90: list[float | None], realized: list[float]) -> float | None:
    """Share of realized values inside [p10, p90] (target ≈ 0.80). Points without an interval are skipped."""
    pts = [(lo, hi, r) for lo, hi, r in zip(p10, p90, realized) if lo is not None and hi is not None]
    return sum(1 for lo, hi, r in pts if lo <= r <= hi) / len(pts) if pts else None


def brier(confidence: list[float], pred_dir: list[int], realized: list[float]) -> float | None:
    """mean((stated probability the direction is right − 1[direction right])²), non-zero predictions only."""
    pts = [(c, 1.0 if d == _sign(r) else 0.0) for c, d, r in zip(confidence, pred_dir, realized) if d != 0]
    return float(np.mean([(c - o) ** 2 for c, o in pts])) if pts else None


def skill_vs_zero(mae_model: float | None, mae_zero: float | None) -> float | None:
    """1 − MAE/MAE_zero: > 0 means better than predicting no change."""
    if mae_model is None or not mae_zero:
        return None
    return round(1 - mae_model / mae_zero, 6)


def bootstrap_ci(values: list[float], n_boot: int = 2000, level: float = 0.90, seed: int = 42) -> list[float] | None:
    """Percentile bootstrap CI of the mean. Seeded, so the scoreboard is identical on every rerun."""
    if not values:
        return None
    rng = np.random.default_rng(seed)
    arr = np.asarray(values, dtype=float)
    means = rng.choice(arr, size=(n_boot, len(arr)), replace=True).mean(axis=1)
    lo, hi = np.quantile(means, [(1 - level) / 2, 1 - (1 - level) / 2])
    return [round(float(lo), 4), round(float(hi), 4)]
