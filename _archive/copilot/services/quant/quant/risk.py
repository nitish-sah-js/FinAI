from __future__ import annotations
import numpy as np
import pandas as pd
from scipy import stats

N_BINS, N_PATHS_OUT = 30, 20


def _hist(pnl: np.ndarray) -> dict:
    counts, edges = np.histogram(pnl, bins=N_BINS)
    return {"bin_edges": [round(float(e), 2) for e in edges], "counts": [int(c) for c in counts]}


def _pack(method, h, c, V, var, cvar, paths, pnl_hist) -> dict:
    return {"method": method, "horizon_days": int(h), "confidence_level": c, "var_inr": round(float(var), 2),
            "cvar_inr": round(float(cvar), 2), "var_pct": round(float(var) / V, 6), "paths": paths, "pnl_hist": pnl_hist}


def _rp(returns: pd.DataFrame, weights: np.ndarray) -> pd.Series:
    return pd.Series(returns.values @ np.asarray(weights, dtype=float), index=returns.index)


def historical(returns: pd.DataFrame, weights, V: float, h: int, c: float) -> dict:
    """Overlapping h-day sums of daily portfolio returns; VaR = -q(1-c)*V; CVaR = -mean(R | R<=q)*V."""
    rp = _rp(returns, weights)
    R = rp.rolling(h).sum().dropna() if h > 1 else rp
    q = float(np.quantile(R.values, 1 - c))
    tail = R[R <= q]
    var, cvar = -q * V, -float(tail.mean()) * V
    last = R.values[-N_PATHS_OUT:]
    paths = []
    for end in range(len(rp) - len(last), len(rp)):          # last 20 overlapping h-day windows
        w = rp.values[max(0, end - h + 1): end + 1]
        paths.append([1.0] + [round(float(x), 5) for x in np.cumprod(1 + w)])
    return _pack("historical", h, c, V, var, cvar, paths, _hist(R.values * V))


def parametric(returns: pd.DataFrame, weights, V: float, h: int, c: float) -> dict:
    """VaR = (-mu*h + z_c*sigma*sqrt(h))*V with portfolio mu, sigma from weights and covariance."""
    w = np.asarray(weights, dtype=float)
    mu = float(returns.mean().values @ w)
    sig = float(np.sqrt(w @ returns.cov().values @ w))
    z = float(stats.norm.ppf(c))
    mu_h, sig_h = mu * h, sig * np.sqrt(h)
    var = (-mu_h + z * sig_h) * V
    # CVaR for a normal: -mu_h + sigma_h * phi(z)/(1-c)
    cvar = (-mu_h + sig_h * stats.norm.pdf(z) / (1 - c)) * V
    edges = np.linspace(mu_h - 4 * sig_h, mu_h + 4 * sig_h, N_BINS + 1)
    probs = np.diff(stats.norm.cdf(edges, mu_h, sig_h))
    hist = {"bin_edges": [round(float(e * V), 2) for e in edges], "counts": [int(round(p * 10_000)) for p in probs]}
    return _pack("parametric", h, c, V, var, cvar, [], hist)


def montecarlo(returns: pd.DataFrame, weights, V: float, h: int, c: float, n_paths: int = 10_000, seed: int = 42) -> dict:
    """Multivariate-normal daily returns N(mu, Sigma) via Cholesky (jitter 1e-10*I if not PD), compounded over h days."""
    w = np.asarray(weights, dtype=float)
    n = len(w)
    mu = returns.mean().values
    cov = np.atleast_2d(returns.cov().values)
    try:
        L = np.linalg.cholesky(cov)
    except np.linalg.LinAlgError:
        L = np.linalg.cholesky(cov + 1e-10 * np.eye(n))
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((n_paths, h, n))
    daily = mu + z @ L.T                                      # (paths, h, assets)
    rel = np.cumprod(1.0 + daily, axis=1) @ w                 # portfolio value / V, per day
    pnl = (rel[:, -1] - 1.0) * V
    q = float(np.quantile(pnl, 1 - c))
    var, cvar = -q, -float(pnl[pnl <= q].mean())
    paths = np.hstack([np.ones((min(N_PATHS_OUT, n_paths), 1)), rel[:N_PATHS_OUT]])
    return _pack("montecarlo", h, c, V, var, cvar, np.round(paths, 5).tolist(), _hist(pnl))
