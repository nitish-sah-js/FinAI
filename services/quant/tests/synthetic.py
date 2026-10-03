"""Synthetic generators with KNOWN answers."""
from __future__ import annotations
import numpy as np
import pandas as pd


def make_market(n: int = 500, seed: int = 0, mu: float = 0.0004, sigma: float = 0.01) -> pd.Series:
    """Benchmark daily (log) returns N(mu, sigma) on business days."""
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.Series(np.random.default_rng(seed).normal(mu, sigma, n), index=idx, name="bench")


def make_stock(bench: pd.Series, beta: float = 1.3, alpha: float = 0.0, noise: float = 0.005, seed: int = 1) -> pd.Series:
    eps = np.random.default_rng(seed).normal(0, noise, len(bench))
    return pd.Series(alpha + beta * bench.values + eps, index=bench.index, name="stock")


def inject_event(stock: pd.Series, day: int, jump: float = 0.05) -> pd.Series:
    s = stock.copy()
    s.iloc[day] += jump
    return s


def prices_from_returns(r: pd.Series, start: float = 100.0, log: bool = True, name: str | None = None) -> pd.Series:
    """Price series with one extra leading row so that returns recover exactly `r`."""
    idx = pd.DatetimeIndex([r.index[0] - pd.offsets.BDay(1), *r.index])
    path = np.exp(np.cumsum(r.values)) if log else np.cumprod(1 + r.values)
    p = pd.Series(np.concatenate([[start], start * path]), index=idx, name=name or r.name)
    return p


def standardized(n: int = 5000, sigma: float = 0.01, seed: int = 3) -> np.ndarray:
    x = np.random.default_rng(seed).standard_normal(n)
    return (x - x.mean()) / x.std(ddof=1) * sigma
