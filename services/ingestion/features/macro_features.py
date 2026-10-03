from __future__ import annotations
import pandas as pd


def pct_change_n(series: pd.Series, n: int = 5) -> float | None:
    s = series.dropna()
    if len(s) <= n:
        return None
    return float(s.iloc[-1] / s.iloc[-1 - n] - 1)


def bps_change(new_rate: float, old_rate: float) -> int:
    return round((new_rate - old_rate) * 100)
