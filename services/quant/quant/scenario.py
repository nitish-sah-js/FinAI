from __future__ import annotations
import numpy as np
import pandas as pd
from copilot_common.models import Portfolio
from .exposure import sector_sens

# factor -> factor_returns column (measured by OLS when the series is available)
MARKET_FACTORS = {"nifty", "crude", "usd_inr", "us10y"}
# judgment factors (non-traded) and the heatmap column they map to; also fallback for market factors without data
JUDGMENT_COL = {"monsoon_rain": "agri", "heatwave": "weather", "agri_stress": "agri", "repo_bps": "rates",
                "crude": "crude", "usd_inr": "usd_inr", "us10y": "rates"}
SUPPORTED = MARKET_FACTORS | set(JUDGMENT_COL)


T_HALF = 2.0          # |t| at which the OLS estimate gets half the weight (shrinkage below)


def _ols_beta(y: pd.Series, x: pd.Series, rows: int = 250, min_n: int = 30) -> float | None:
    fit = ols_fit(y, x, rows, min_n)
    return None if fit is None else fit["beta"]


def ols_fit(y: pd.Series, x: pd.Series, rows: int = 250, min_n: int = 30) -> dict | None:
    """OLS slope with its standard error and t-statistic (y = a + b·x + e)."""
    d = pd.concat({"y": y, "x": x}, axis=1).dropna().tail(rows)
    n = len(d)
    if n < min_n or d["x"].var() == 0:
        return None
    b = float(d["y"].cov(d["x"]) / d["x"].var())
    a = float(d["y"].mean() - b * d["x"].mean())
    resid = d["y"] - (a + b * d["x"])
    s2 = float((resid ** 2).sum() / (n - 2))
    se = float(np.sqrt(s2 / (((d["x"] - d["x"].mean()) ** 2).sum())))
    return {"beta": b, "se": se, "t": b / se if se > 0 else 0.0, "n": n}


def shrink(fit: dict, prior: float) -> tuple[float, float]:
    """Shrink a noisy OLS beta toward the prior, weighted by its own significance: w = t²/(t² + T_HALF²).
    On 250 days, RELIANCE/ITC/HDFCBANK crude betas came out −0.04…−0.07 (all NEGATIVE, i.e. noise): left raw they say
    crude +10% hurts a refiner. A significant beta (Nifty, |t|≈20) keeps w ≈ 0.99, so it is effectively OLS."""
    t2 = fit["t"] ** 2
    w = t2 / (t2 + T_HALF ** 2)
    return w * fit["beta"] + (1 - w) * prior, w


def judgment_beta(sensitivity: dict, sector: str | None, factor: str) -> float:
    if factor == "nifty":
        return 1.0
    col = JUDGMENT_COL[factor]
    sens = sector_sens(sensitivity, sector).get(col, 0.0)
    signs = sensitivity.get("_signs", {}).get(factor, {})
    sign = signs.get(sector or "Other", signs.get("default", 1))
    scale = sensitivity.get("_scale", {}).get(factor, 0.1)
    return float(sens * sign * scale)


def run(portfolio: Portfolio, prices: pd.DataFrame, factor_returns: pd.DataFrame, shocks: dict[str, float],
        sensitivity: dict) -> dict:
    """Linear factor model dP/P = sum_f beta_f * shock_f/100 (shock in % points; repo_bps/us10y in bps -> beta per 1pp)."""
    last = {c: float(prices[c].dropna().iloc[-1]) for c in prices.columns if prices[c].notna().any()}
    held = [h for h in portfolio.holdings if h.ticker in last]
    rets = prices.pct_change()
    V = sum(h.qty * last[h.ticker] for h in held)
    by_ticker, total = [], 0.0
    judged: set[str] = set()
    shrunk_info: dict[str, list] = {}
    for h in held:
        value = h.qty * last[h.ticker]
        betas, methods, stats_, change = {}, {}, {}, 0.0
        for f, s in shocks.items():
            b, fit = None, None
            if f in MARKET_FACTORS and f in factor_returns.columns:
                fit = ols_fit(rets[h.ticker], factor_returns[f])
            if fit is None:
                b, m = judgment_beta(sensitivity, h.sector, f), "judgment"
                judged.add(f)
            else:
                b, w = shrink(fit, judgment_beta(sensitivity, h.sector, f))
                m = "ols" if w >= 0.8 else "shrunk"
                stats_[f] = {"beta_ols": round(fit["beta"], 4), "t": round(fit["t"], 2), "n": fit["n"], "weight_ols": round(w, 3)}
                if w < 0.5:                          # mostly the prior: count it as judgment for confidence + warnings
                    judged.add(f)
                    shrunk_info.setdefault(f, []).append(f"{h.ticker} t={fit['t']:.1f}")
            betas[f], methods[f] = round(b, 4), m
            change += b * s / 100.0
        kinds = set(methods.values())
        by_ticker.append({"ticker": h.ticker, "pnl_inr": round(value * change, 2), "pnl_pct": round(change, 5),
                          "betas": betas, "methods": methods, "beta_stats": stats_,
                          "method": "mixed" if len(kinds) > 1 else (kinds.pop() if kinds else "ols")})
        total += value * change
    return {"shocks": shocks, "pnl_inr": round(total, 2), "pnl_pct": round(total / V, 5) if V else 0.0,
            "by_ticker": by_ticker, "judgment_factors": sorted(judged), "portfolio_value_inr": round(V, 2),
            "insignificant_betas": shrunk_info}
