"""Phase 2 (and gated Phase 3) of the hedge review: does the hedge actually help on past analog events?

For each event the hedge is fitted ONLY on the `lookback` trading days before it (out of sample), then the unhedged and
hedged portfolio paths are compared over the next `h` trading days: return and max drawdown. Futures are modelled on the
spot index (basis and carry ignored, stated in the output). With a handful of events this is a sanity check, not proof:
the output always carries n and a range, and confidence is capped low.

Phase 3 (optional) is evaluated here, not deployed: a ridge-regularised, signed, capped minimum-variance hedge over
India-tradable instruments. It is reported next to the baseline and is "adopt: true" only if it beats the baseline on these
events with n ≥ MIN_N_ADOPT. /hedge_proposals keeps proposing the baseline either way.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_N_ADOPT = 5
# Optimizer universe: only instruments an Indian investor can trade. US futures are price PROXIES for the MCX contracts.
INSTRUMENTS = {
    "^NSEI":   {"name": "NIFTY futures", "venue": "NSE F&O", "cap": 1.5, "proxy": None},
    "^NSEBANK": {"name": "BANKNIFTY futures", "venue": "NSE F&O", "cap": 1.0, "proxy": None},
    "INR=X":   {"name": "USDINR futures", "venue": "NSE currency derivatives", "cap": 0.5, "proxy": None},
    "CL=F":    {"name": "MCX crude oil", "venue": "MCX", "cap": 0.3, "proxy": "WTI (CL=F) price as proxy: basis risk"},
    "GC=F":    {"name": "MCX gold", "venue": "MCX", "cap": 0.3, "proxy": "COMEX gold (GC=F) price as proxy: basis risk"},
}


def _max_dd(path: np.ndarray) -> float:
    """Max drawdown of a value path that starts at 1.0, as a positive fraction."""
    peak = np.maximum.accumulate(path)
    return float(np.max(1 - path / peak))


def ridge_weights(rp: pd.Series, X: pd.DataFrame, ridge: float, caps: dict[str, float]) -> dict[str, float]:
    """argmin Var(rp − X·w) + λ·||w||², λ scaled to the average instrument variance; then signed bounds |w_j| ≤ cap_j.
    Ridge keeps noisy, offsetting positions out (the review's '31% in crude futures' failure)."""
    C = X.cov().values
    c = np.array([rp.cov(X[j]) for j in X.columns])
    lam = ridge * float(np.trace(C)) / max(len(c), 1)
    w = np.linalg.solve(C + lam * np.eye(len(c)), c)
    return {j: float(np.clip(wj, -caps[j], caps[j])) for j, wj in zip(X.columns, w)}


def validate(prices: pd.DataFrame, qty: dict[str, float], underlying: str, events: list[dict], h: int = 5,
             lookback: int = 250, compare_optimizer: bool = True, optimizer_instruments: list[str] | None = None,
             ridge: float = 0.5) -> dict:
    rows, opt_rows, skipped = [], [], []
    inst = [i for i in (optimizer_instruments or list(INSTRUMENTS)) if i in INSTRUMENTS and i in prices.columns]
    prices = prices.sort_index().ffill(limit=5)        # fill holiday gaps BEFORE slicing, so the anchor row is never NaN
    for ev in events:
        eid, edate = ev.get("event_id"), pd.Timestamp(str(ev.get("event_date"))[:10])
        before = prices[prices.index < edate]
        held = [t for t in qty if t in prices.columns and before[t].tail(lookback + 1).notna().sum() > lookback * 0.8]
        if underlying not in prices.columns or not held or len(before) < 60:
            skipped.append(f"{eid}: not enough history before {edate.date()}")
            continue
        cols = list(dict.fromkeys(held + [underlying]))          # the hedge index may itself be a holding
        fit = before[cols].tail(lookback + 1).ffill().pct_change().dropna()
        base = before[cols].ffill().iloc[-1]
        vals = np.array([qty[t] * base[t] for t in held])
        V = float(vals.sum())
        w = vals / V
        rp = pd.Series(fit[held].values @ w, index=fit.index)
        rf = fit[underlying]
        if rf.var() == 0:
            skipped.append(f"{eid}: flat hedge instrument")
            continue
        h_star = float(rp.cov(rf) / rf.var())                      # fitted strictly before the event
        fwd = prices[prices.index >= before.index[-1]][cols].ffill().head(h + 1)
        if len(fwd) < h + 1 or fwd.isna().any().any():
            skipped.append(f"{eid}: fewer than {h} trading days after the event in the data")
            continue
        path_u = (fwd[held].values @ np.array([qty[t] for t in held])) / V
        path_f = fwd[underlying].values / fwd[underlying].values[0]
        path_h = path_u - h_star * (path_f - 1)                    # short h*·V of the index future
        dd_u, dd_h = _max_dd(path_u), _max_dd(path_h)
        rows.append({"event_id": eid, "event_date": str(edate.date()), "n_holdings": len(held), "hedge_ratio": round(h_star, 4),
                     "ret_unhedged": round(path_u[-1] - 1, 5), "ret_hedged": round(path_h[-1] - 1, 5),
                     "dd_unhedged": round(dd_u, 5), "dd_hedged": round(dd_h, 5),
                     "dd_reduction_pp": round((dd_u - dd_h) * 100, 3), "improved": bool(dd_h < dd_u)})

        if compare_optimizer and inst:
            Xf = before[inst].tail(lookback + 1).ffill().pct_change().reindex(fit.index).dropna(axis=1, how="any")
            cols = list(Xf.columns)
            fwd_i = prices[prices.index >= before.index[-1]][cols].ffill().head(h + 1)
            if cols and len(fwd_i) == h + 1 and not fwd_i.isna().any().any():
                wts = ridge_weights(rp.loc[Xf.index], Xf, ridge, {j: INSTRUMENTS[j]["cap"] for j in cols})
                path_o = path_u - sum(wts[j] * (fwd_i[j].values / fwd_i[j].values[0] - 1) for j in cols)
                dd_o = _max_dd(path_o)
                opt_rows.append({"event_id": eid, "weights": {j: round(v, 4) for j, v in wts.items()},
                                 "dd_reduction_pp": round((dd_u - dd_o) * 100, 3), "improved": bool(dd_o < dd_u),
                                 "ret_hedged": round(path_o[-1] - 1, 5)})

    out = {"underlying": underlying, "horizon_days": h, "lookback_days": lookback, "method": "min_variance, fitted out of sample",
           "events": rows, "skipped": skipped, **_agg(rows),
           "assumptions": ["futures modelled on the spot index (basis and carry ignored)",
                           "hedge ratio fitted on the lookback window BEFORE each event only",
                           "small n: a sanity check, not proof"]}
    if compare_optimizer:
        opt = {"instruments": {j: INSTRUMENTS[j] for j in inst}, "ridge": ridge, "events": opt_rows, **_agg(opt_rows)}
        base, o = out, opt
        beats = (o["n"] >= MIN_N_ADOPT and o["median_dd_reduction_pp"] is not None and base["median_dd_reduction_pp"] is not None
                 and o["median_dd_reduction_pp"] > base["median_dd_reduction_pp"] and o["n_improved"] >= base["n_improved"])
        opt["beats_baseline"] = bool(beats)
        opt["adopt"] = bool(beats)
        if beats:
            opt["decision"] = "optimizer beats the single-index baseline on these events"
        elif o["n"] < MIN_N_ADOPT:
            opt["decision"] = f"keep the single-index baseline: only {o['n']} events (needs ≥ {MIN_N_ADOPT} to adopt)"
        else:
            opt["decision"] = (f"keep the single-index baseline: optimizer median {o['median_dd_reduction_pp']} pp vs "
                               f"baseline {base['median_dd_reduction_pp']} pp drawdown reduction")
        out["optimizer"] = opt
    return out


def _agg(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0, "n_improved": 0, "median_dd_reduction_pp": None, "range_dd_reduction_pp": None}
    d = np.array([r["dd_reduction_pp"] for r in rows])
    lo, hi = (np.quantile(d, [0.1, 0.9]) if n >= 5 else (d.min(), d.max()))
    return {"n": n, "n_improved": int(sum(r["improved"] for r in rows)),
            "median_dd_reduction_pp": round(float(np.median(d)), 3),
            "range_dd_reduction_pp": [round(float(lo), 3), round(float(hi), 3)],
            "range_kind": "p10–p90" if n >= 5 else "min–max (n < 5)"}
