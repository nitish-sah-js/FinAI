from __future__ import annotations
import pandas as pd
from copilot_common.models import Portfolio
from .exposure import sector_sens

# factor -> factor_returns column (measured by OLS when the series is available)
MARKET_FACTORS = {"nifty", "crude", "usd_inr", "us10y"}
# judgment factors (non-traded) and the heatmap column they map to; also fallback for market factors without data
JUDGMENT_COL = {"monsoon_rain": "agri", "heatwave": "weather", "agri_stress": "agri", "repo_bps": "rates",
                "crude": "crude", "usd_inr": "usd_inr", "us10y": "rates"}
SUPPORTED = MARKET_FACTORS | set(JUDGMENT_COL)


def _ols_beta(y: pd.Series, x: pd.Series, rows: int = 250, min_n: int = 30) -> float | None:
    d = pd.concat({"y": y, "x": x}, axis=1).dropna().tail(rows)
    if len(d) < min_n or d["x"].var() == 0:
        return None
    return float(d["y"].cov(d["x"]) / d["x"].var())


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
    for h in held:
        value = h.qty * last[h.ticker]
        betas, methods, change = {}, {}, 0.0
        for f, s in shocks.items():
            b = None
            if f in MARKET_FACTORS and f in factor_returns.columns:
                b = _ols_beta(rets[h.ticker], factor_returns[f])
            if b is None:
                b, m = judgment_beta(sensitivity, h.sector, f), "judgment"
                judged.add(f)
            else:
                m = "ols"
            betas[f], methods[f] = round(b, 4), m
            change += b * s / 100.0
        kinds = set(methods.values())
        by_ticker.append({"ticker": h.ticker, "pnl_inr": round(value * change, 2), "pnl_pct": round(change, 5),
                          "betas": betas, "methods": methods,
                          "method": "mixed" if len(kinds) > 1 else (kinds.pop() if kinds else "ols")})
        total += value * change
    return {"shocks": shocks, "pnl_inr": round(total, 2), "pnl_pct": round(total / V, 5) if V else 0.0,
            "by_ticker": by_ticker, "judgment_factors": sorted(judged), "portfolio_value_inr": round(V, 2)}
