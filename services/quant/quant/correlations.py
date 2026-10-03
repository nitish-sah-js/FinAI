from __future__ import annotations
import itertools
import numpy as np
import pandas as pd


def run(returns: pd.DataFrame, lags: list[int], method: str = "pearson") -> dict:
    """corr(r_a,t , r_b,t-k) for every ordered-as-listed pair (a before b) and every lag k."""
    pairs = []
    for a, b in itertools.combinations(list(returns.columns), 2):
        for k in lags:
            d = pd.concat({"a": returns[a], "b": returns[b].shift(k)}, axis=1).dropna()
            n = len(d)
            if n < 10:
                continue
            c = d["a"].corr(d["b"], method=method)
            if c is None or not np.isfinite(c):
                continue
            pairs.append({"a": a, "b": b, "lag": int(k), "corr": round(float(c), 4), "n": int(n), "weak": bool(abs(c) < 0.1)})
    return {"pairs": pairs}
