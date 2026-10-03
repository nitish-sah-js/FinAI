import numpy as np
import pandas as pd
from quant import risk
from synthetic import standardized


def _one_asset():
    return pd.DataFrame({"A": standardized(5000, 0.01)})


def test_parametric_known_answer():
    r = risk.parametric(_one_asset(), [1.0], 1e6, 1, 0.95)
    assert abs(r["var_inr"] - 16449) / 16449 < 0.02


def test_montecarlo_close_to_parametric_and_deterministic():
    df, w = _one_asset(), [1.0]
    p = risk.parametric(df, w, 1e6, 1, 0.95)["var_inr"]
    a = risk.montecarlo(df, w, 1e6, 1, 0.95, 10_000, 42)
    b = risk.montecarlo(df, w, 1e6, 1, 0.95, 10_000, 42)
    c = risk.montecarlo(df, w, 1e6, 1, 0.95, 10_000, 7)
    assert abs(a["var_inr"] - p) / p < 0.05
    assert a == b                                   # same seed -> identical output
    assert a["var_inr"] != c["var_inr"]
    assert len(a["paths"]) == 20 and len(a["paths"][0]) == 2
    assert len(a["pnl_hist"]["counts"]) == 30 and len(a["pnl_hist"]["bin_edges"]) == 31


def test_historical_cvar_ge_var_and_multiday():
    rng = np.random.default_rng(5)
    df = pd.DataFrame(rng.normal(0.0003, 0.012, (800, 3)), columns=list("ABC"))
    w = np.array([0.5, 0.3, 0.2])
    for h in (1, 5):
        r = risk.historical(df, w, 5e5, h, 0.95)
        assert r["cvar_inr"] >= r["var_inr"] > 0
        assert abs(r["var_pct"] - r["var_inr"] / 5e5) < 1e-5
    assert risk.historical(df, w, 5e5, 5, 0.95)["var_inr"] > risk.historical(df, w, 5e5, 1, 0.95)["var_inr"]


def test_montecarlo_handles_singular_covariance():
    x = standardized(500, 0.01)
    df = pd.DataFrame({"A": x, "B": x})             # perfectly collinear -> not PD
    r = risk.montecarlo(df, [0.5, 0.5], 1e6, 5, 0.95, 2000, 1)
    assert r["var_inr"] > 0
