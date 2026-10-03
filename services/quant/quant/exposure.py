from __future__ import annotations
import numpy as np
import pandas as pd
from copilot_common.models import Portfolio

COLS = ["weather", "agri", "crude", "rates", "usd_inr"]


def sector_sens(sensitivity: dict, sector: str | None) -> dict:
    s = sector or "Other"
    row = sensitivity.get(s) if not s.startswith("_") else None
    return row or sensitivity.get("Other") or {c: 0.0 for c in COLS}


def beta_vs(stock: pd.Series, bench: pd.Series, rows: int = 250) -> float | None:
    d = pd.concat({"s": stock.pct_change(), "b": bench.pct_change()}, axis=1).dropna().tail(rows)
    if len(d) < 30 or d["b"].var() == 0:
        return None
    return float(d["s"].cov(d["b"]) / d["b"].var())


def run(portfolio: Portfolio, prices: pd.DataFrame, bench: pd.Series | None, sensitivity: dict) -> dict:
    last = {c: float(prices[c].dropna().iloc[-1]) for c in prices.columns if prices[c].notna().any()}
    held = [h for h in portfolio.holdings if h.ticker in last]
    vals = {h.ticker: h.qty * last[h.ticker] for h in held}
    V = sum(vals.values())
    by_ticker, by_sector = [], {}
    for h in held:
        w = vals[h.ticker] / V
        sens = sector_sens(sensitivity, h.sector)
        b = beta_vs(prices[h.ticker], bench) if bench is not None else None
        by_ticker.append({"ticker": h.ticker, "weight": round(w, 4), "beta": None if b is None else round(b, 3),
                          "weather_sens": sens.get("weather", 0.0), "agri_sens": sens.get("agri", 0.0)})
        by_sector[h.sector or "Other"] = by_sector.get(h.sector or "Other", 0.0) + w
    rows = list(by_sector)
    values = [[round(by_sector[s] * sector_sens(sensitivity, s).get(c, 0.0), 4) for c in COLS] for s in rows]
    return {"by_sector": {s: round(w, 4) for s, w in by_sector.items()}, "by_ticker": by_ticker,
            "heatmap": {"rows": rows, "cols": COLS, "values": values, "row_weights": {s: round(w, 4) for s, w in by_sector.items()}},
            "portfolio_value_inr": round(V, 2)}
