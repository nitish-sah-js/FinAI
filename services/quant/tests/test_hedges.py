import pandas as pd
from copilot_common.models import Holding, Portfolio
from quant import hedges
from synthetic import make_market, make_stock, prices_from_returns
from datetime import date


def _setup(noise=0.001, beta=1.0):
    bench = make_market(500, seed=31)
    stock = make_stock(bench, beta=beta, noise=noise, seed=32)
    px = pd.DataFrame({"S.NS": prices_from_returns(stock, 100.0, log=False),
                       "^NSEI": prices_from_returns(bench, 1000.0, log=False)})
    pf = Portfolio(holdings=[Holding(ticker="S.NS", qty=1000, sector="Energy")])
    return pf, px


def test_beta_one_portfolio_hedge_ratio_and_variance_reduction():
    pf, px = _setup()
    r = hedges.propose(pf, px, ["^NSEI"], "min_variance", {"^NSEI": 1}, True, 5, ref_date=date(2026, 10, 3))
    h1 = r["proposals"][0]
    assert h1["unit"] == "lots" and h1["side"] == "sell"
    assert abs(h1["hedge_ratio"] - 1.0) < 0.1
    assert r["post_var_inr"] < 0.2 * r["pre_var_inr"]
    assert abs(r["portfolio_beta"] - 1.0) < 0.1


def test_beta_neutral_and_put_leg():
    pf, px = _setup(beta=0.8)
    r = hedges.propose(pf, px, ["^NSEI"], "beta_neutral", {"^NSEI": 1}, True, 5, ref_date=date(2026, 10, 3))
    assert r["proposals"][0]["sizing_method"] == "beta"
    assert abs(r["proposals"][0]["hedge_ratio"] - 0.8) < 0.1
    put = r["proposals"][1]
    assert put["instrument"].endswith("PE") and put["est_cost_inr"] > 0 and put["sizing_method"] == "delta"


def test_small_portfolio_falls_back_to_notional():
    pf, px = _setup()
    r = hedges.propose(pf, px, ["^NSEI"], "min_variance", {"^NSEI": 10_000}, False, 5)
    assert r["proposals"][0]["unit"] == "notional_inr"
    assert any("too small" in w for w in r["warnings"])


def test_reduce_var_target():
    pf, px = _setup()
    r = hedges.propose(pf, px, ["^NSEI"], "reduce_var_pct", {"^NSEI": 1}, False, 5, reduce_var_pct=30)
    assert r["post_var_inr"] <= 0.75 * r["pre_var_inr"]


def test_expiry_is_last_tuesday():
    d = hedges.next_monthly_expiry(date(2026, 10, 3), weekday=1)
    assert d == date(2026, 10, 27) and d.weekday() == 1
    assert hedges.next_monthly_expiry(date(2026, 10, 27), weekday=1) == date(2026, 11, 24)
