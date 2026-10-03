from __future__ import annotations
import math
from scipy.stats import norm


def bs(spot: float, strike: float, vol: float, rate: float, T: float, kind: str = "put") -> dict:
    """Black-Scholes (no dividends). T in years. Returns price and Greeks."""
    T = max(T, 1e-8)
    vol = max(vol, 1e-8)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * T) / (vol * math.sqrt(T))
    d2 = d1 - vol * math.sqrt(T)
    disc = math.exp(-rate * T)
    if kind == "call":
        price, delta = spot * norm.cdf(d1) - strike * disc * norm.cdf(d2), norm.cdf(d1)
    else:
        price, delta = strike * disc * norm.cdf(-d2) - spot * norm.cdf(-d1), norm.cdf(d1) - 1.0
    return {"price": float(price), "delta": float(delta), "gamma": float(norm.pdf(d1) / (spot * vol * math.sqrt(T))),
            "vega": float(spot * norm.pdf(d1) * math.sqrt(T)), "d1": float(d1), "d2": float(d2)}
