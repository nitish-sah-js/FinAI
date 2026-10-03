from __future__ import annotations
from datetime import date
import numpy as np
import pandas as pd
from scipy import stats
from .data import DataError


def run(prices: pd.Series, bench: pd.Series, event_date: date, est_window=(-120, -11), event_window=(-1, 5)) -> dict:
    """Market-model event study on log returns (06 §3)."""
    df = pd.concat({"r": np.log(prices / prices.shift(1)), "m": np.log(bench / bench.shift(1))}, axis=1).dropna()
    if df.empty:
        raise DataError("no overlapping returns for ticker and benchmark")
    t0 = int(df.index.searchsorted(pd.Timestamp(event_date)))  # first trading day >= event_date
    if t0 >= len(df):
        raise DataError("event date is after the last available price")
    e_lo, e_hi = t0 + est_window[0], t0 + est_window[1]
    w_lo, w_hi = t0 + event_window[0], t0 + event_window[1]
    if e_lo < 0 or w_hi >= len(df):
        raise DataError("not enough price history around the event for the requested windows")
    est = df.iloc[e_lo:e_hi + 1]
    n_est = len(est)
    if n_est < 30:
        raise DataError(f"estimation window too short (n={n_est})")
    beta, alpha = np.polyfit(est["m"].values, est["r"].values, 1)
    resid = est["r"].values - (alpha + beta * est["m"].values)
    sigma = float(resid.std(ddof=2))
    ev = df.iloc[w_lo:w_hi + 1]
    ar = ev["r"].values - (alpha + beta * ev["m"].values)
    car = float(ar.sum())
    L = len(ar)
    t = car / (sigma * np.sqrt(L)) if sigma > 0 else 0.0
    p = float(2 * (1 - stats.t.cdf(abs(t), df=n_est - 2)))
    days = list(range(event_window[0], event_window[1] + 1))
    return {"ticker": str(prices.name) if prices.name else None, "event_date": pd.Timestamp(event_date).date().isoformat(),
            "window": list(event_window), "alpha": round(float(alpha), 6), "beta": round(float(beta), 4),
            "car": round(car, 6), "car_t": round(float(t), 4), "p_value": round(p, 6),
            "n_est": n_est, "sigma_ar": round(sigma, 6),
            "ar_series": [{"d": d, "ar": round(float(a), 6)} for d, a in zip(days, ar)]}
