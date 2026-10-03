"""Baselines (12 §A4), computed from closes STRICTLY on or before as_of (tested: corrupting later prices changes nothing).

closes = [(YYYY-MM-DD, close)], sorted. Every baseline returns {median, p10, p90, direction}.
"""
from __future__ import annotations

import numpy as np

Z80 = 1.28          # ±1.28σ ≈ an 80 % normal interval
SENT_EPS = 0.05     # |sentiment| below this is "no direction"


def _past(closes: list[tuple[str, float]], as_of: str) -> np.ndarray:
    return np.asarray([c for d, c in closes if d <= as_of[:10]], dtype=float)


def _h_returns(px: np.ndarray, h: int, lookback: int = 250) -> np.ndarray:
    px = px[-(lookback + h):]
    return px[h:] / px[:-h] - 1 if len(px) > h else np.asarray([])


def _scale(closes, as_of, h) -> tuple[float, float]:
    """(mean |h-day return|, std of h-day returns) over the past 250 trading days."""
    r = _h_returns(_past(closes, as_of), h)
    if len(r) < 2:
        return 0.0, 0.0
    return float(np.mean(np.abs(r))), float(np.std(r, ddof=1))


def _pred(direction: int, mag: float, sd: float) -> dict:
    med = direction * mag
    return {"median": round(med, 6), "p10": round(med - Z80 * sd, 6), "p90": round(med + Z80 * sd, 6),
            "direction": direction}


def price_only(closes: list[tuple[str, float]], as_of: str, h: int = 5) -> dict:
    """Momentum: direction = sign(20-day return before as_of)."""
    px = _past(closes, as_of)
    mag, sd = _scale(closes, as_of, h)
    if len(px) < 21:
        return _pred(0, 0.0, sd)
    r20 = px[-1] / px[-21] - 1
    return _pred(1 if r20 > 0 else -1 if r20 < 0 else 0, mag, sd)


def sentiment_only(closes: list[tuple[str, float]], as_of: str, sentiment: float | None, h: int = 5) -> dict:
    """direction = sign(news sentiment ≤ as_of); same magnitude and interval as price_only."""
    mag, sd = _scale(closes, as_of, h)
    s = sentiment or 0.0
    return _pred(0 if abs(s) < SENT_EPS else (1 if s > 0 else -1), mag, sd)


def zero(closes: list[tuple[str, float]], as_of: str, h: int = 5) -> dict:
    """No-change reference for MAE: median 0, same interval width."""
    _, sd = _scale(closes, as_of, h)
    return _pred(0, 0.0, sd)
