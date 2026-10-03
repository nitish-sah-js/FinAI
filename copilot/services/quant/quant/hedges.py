from __future__ import annotations
import calendar
import os
from datetime import date, timedelta
import numpy as np
import pandas as pd
from copilot_common.models import Portfolio
from . import options

RISK_FREE = 0.065
CONF = 0.95
OTM_PCT = 0.04                                   # protective put strike 4% OTM (spec: 3-5%)
EXPIRY_WEEKDAY = int(os.environ.get("QUANT_EXPIRY_WEEKDAY", 1))   # 1 = Tuesday: NSE monthly index expiry = last Tuesday (verify)
NAMES = {"^NSEI": "NIFTY", "^NSEBANK": "BANKNIFTY"}
STRIKE_STEP = {"^NSEI": 50, "^NSEBANK": 100}


def next_monthly_expiry(ref: date, weekday: int = EXPIRY_WEEKDAY) -> date:
    y, m = ref.year, ref.month
    for _ in range(3):
        last = date(y, m, calendar.monthrange(y, m)[1])
        d = last - timedelta(days=(last.weekday() - weekday) % 7)
        if d > ref:
            return d
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    raise RuntimeError("expiry search failed")


def _hvar(x: np.ndarray, c: float = CONF) -> float:
    return -float(np.quantile(x, 1 - c))


def _hsum(s: pd.Series, h: int) -> pd.Series:
    return s.rolling(h).sum().dropna() if h > 1 else s


def _stats(rets: pd.DataFrame, w: np.ndarray, f: str, h: int, V: float) -> dict | None:
    rp = pd.Series(rets[[c for c in rets.columns if c != f]].values @ w, index=rets.index)
    rf = rets[f]
    if len(rp) < 60 or rf.var() == 0:
        return None
    t250 = rets.tail(250)
    betas = np.array([t250[c].cov(t250[f]) / t250[f].var() for c in rets.columns if c != f])
    beta_p = float(w @ betas)
    rph, rfh = _hsum(rp, h), _hsum(rf, h)
    h_star = float(rph.cov(rfh) / rfh.var())
    pre = _hvar(rph.values) * V
    return {"rp_h": rph, "rf_h": rfh, "beta_p": beta_p, "h_star": h_star, "pre_var": pre}


def _post_var(st: dict, ratio: float, V: float) -> float:
    return _hvar((st["rp_h"] - ratio * st["rf_h"]).values) * V


def propose(portfolio: Portfolio, prices: pd.DataFrame, candidates: list[str], target: str, lot_sizes: dict,
            allow_options: bool, h: int, reduce_var_pct: float = 30.0, ref_date: date | None = None,
            evidence_ids: list[str] | None = None) -> dict:
    ev_ids = list(evidence_ids or [])
    warnings: list[str] = []
    held = [x for x in portfolio.holdings if x.ticker in prices.columns]
    hold_cols = [x.ticker for x in held]
    qty = {x.ticker: x.qty for x in held}
    best = None
    cand_stats = []
    for f in candidates:
        if f not in prices.columns:
            warnings.append(f"no price data for hedge candidate {f}")
            continue
        px = prices[hold_cols + [f]].dropna()
        last = px.iloc[-1]
        vals = np.array([qty[c] * last[c] for c in hold_cols])
        V = float(vals.sum())
        w = vals / V
        rets = px.pct_change().dropna()
        st = _stats(rets[hold_cols + [f]], w, f, h, V)
        if st is None:
            warnings.append(f"insufficient history for hedge candidate {f}")
            continue
        if target == "beta_neutral":
            ratio, method = st["beta_p"], "beta"
        elif target == "reduce_var_pct":
            ratio, method = st["h_star"], "fixed"
            goal = st["pre_var"] * (1 - reduce_var_pct / 100.0)
            for k in np.arange(0.05, 1.0001, 0.05):
                if _post_var(st, k * st["h_star"], V) <= goal:
                    ratio = float(k * st["h_star"])
                    break
            else:
                warnings.append(f"{f}: cannot reach {reduce_var_pct:.0f}% VaR reduction; using full min-variance ratio")
        else:
            ratio, method = st["h_star"], "min_variance"
        post_theory = _post_var(st, ratio, V)
        cand_stats.append({"underlying": f, "beta_p": round(st["beta_p"], 4), "h_star": round(st["h_star"], 4),
                           "post_var_inr": round(post_theory, 2)})
        if best is None or post_theory < best["post_theory"]:
            best = dict(f=f, V=V, st=st, ratio=ratio, method=method, level=float(last[f]), post_theory=post_theory, px=px)
    if best is None:
        return {"proposals": [], "pre_var_inr": None, "post_var_inr": None, "portfolio_beta": None,
                "candidate_stats": cand_stats, "warnings": warnings or ["no usable hedge candidate"]}

    f, V, st, ratio, level = best["f"], best["V"], best["st"], best["ratio"], best["level"]
    name = NAMES.get(f, f.lstrip("^"))
    lot = lot_sizes.get(f)
    ref = ref_date or date.today()
    expiry = next_monthly_expiry(ref)
    mon = expiry.strftime("%b").upper()
    proposals = []

    # ---- futures leg
    side = "sell" if ratio >= 0 else "buy"
    notional = abs(ratio) * V
    eff_ratio = abs(ratio)
    lots_f = notional / (level * lot) if lot else 0.0
    if lot and lots_f >= 0.5:
        qty_f, unit = float(max(1, round(lots_f))), "lots"
        eff_ratio = qty_f * lot * level / V
        if abs(eff_ratio - abs(ratio)) / max(abs(ratio), 1e-9) > 0.25:
            warnings.append(f"lot rounding makes the {name} futures hedge {eff_ratio:.2f}x vs target {abs(ratio):.2f}x (portfolio is small relative to one lot)")
    else:
        qty_f, unit = round(notional, 2), "notional_inr"
        why = f"portfolio too small for one {name} lot" if lot else f"no lot size known for {f}"
        warnings.append(why + " - hedge shown as notional (paper trading only)")
    signed = eff_ratio if side == "sell" else -eff_ratio
    post_eff = _post_var(st, signed, V)
    method_txt = {"beta": f"beta-neutral h=beta_p={st['beta_p']:.2f}",
                  "min_variance": f"min-variance h*={st['h_star']:.2f} on {h}d returns",
                  "fixed": f"scaled to cut VaR {reduce_var_pct:.0f}% (h={ratio:.2f})"}[best["method"]]
    proposals.append({"hedge_id": "h1", "instrument": f"{name} {mon} FUT {'short' if side == 'sell' else 'long'}",
                      "underlying": f, "side": side, "quantity": qty_f, "unit": unit, "hedge_ratio": round(eff_ratio, 4),
                      "est_cost_inr": None, "rationale": f"{method_txt} vs {name}", "sizing_method": best["method"],
                      "evidence_ids": ev_ids})

    # ---- optional protective put (index only, positive beta)
    if allow_options and st["beta_p"] > 0:
        rets_idx = np.log(best["px"][f] / best["px"][f].shift(1)).dropna().tail(20)
        rv = float(rets_idx.std(ddof=1) * np.sqrt(252)) if len(rets_idx) >= 10 else 0.2
        iv = rv * 1.1
        step = STRIKE_STEP.get(f, 50)
        strike = round(level * (1 - OTM_PCT) / step) * step
        T = max((expiry - ref).days, 1) / 365.0
        o = options.bs(level, strike, iv, RISK_FREE, T, "put")
        units = st["beta_p"] * V * 0.5 / (abs(o["delta"]) * level)      # delta*units*S ~ beta_p*V*0.5
        lots_p = units / lot if lot else 0.0
        if lot and lots_p >= 0.5:
            q_p, u_p = float(max(1, round(lots_p))), "lots"
            actual_units = q_p * lot
        else:
            q_p, u_p, actual_units = round(units * level, 2), "notional_inr", units
        proposals.append({"hedge_id": "h2", "instrument": f"{name} {mon} {strike:.0f} PE", "underlying": f, "side": "buy",
                          "quantity": q_p, "unit": u_p, "hedge_ratio": round(abs(o["delta"]) * actual_units * level / V, 4),
                          "est_cost_inr": round(o["price"] * actual_units, 2),
                          "rationale": f"{OTM_PCT:.0%} OTM protective put, BS estimate (IV proxy = 20d RV x 1.1, r={RISK_FREE:.1%}), expiry {expiry.isoformat()}",
                          "sizing_method": "delta", "evidence_ids": ev_ids})
        warnings.append("est_cost_inr is a Black-Scholes ESTIMATE using realised-vol x1.1 as an IV proxy, not a market quote")
    elif allow_options:
        warnings.append("no protective put proposed: portfolio beta <= 0")
    if lot:
        warnings.append("Lot sizes from data/lot_sizes.json - verify on NSE before use. Paper trading only.")
    return {"proposals": proposals, "pre_var_inr": round(st["pre_var"], 2), "post_var_inr": round(post_eff, 2),
            "portfolio_beta": round(st["beta_p"], 4), "candidate_stats": cand_stats, "expiry": expiry.isoformat(),
            "warnings": warnings}
