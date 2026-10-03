import pandas as pd
from copilot_common.models import Holding, Portfolio
from quant import scenario
from quant.data import load_json
from synthetic import make_market, make_stock, prices_from_returns


def _setup(sector="Energy"):
    bench = make_market(400, seed=41)
    stock = make_stock(bench, beta=1.3, noise=0.002, seed=42)
    prices = pd.DataFrame({"S.NS": prices_from_returns(stock, 100.0, log=False)})
    fr = pd.DataFrame({"nifty": prices_from_returns(bench, 1000.0, log=False).pct_change()})
    return Portfolio(holdings=[Holding(ticker="S.NS", qty=100, sector=sector)]), prices, fr


def test_nifty_shock_beta_1_3():
    pf, px, fr = _setup()
    r = scenario.run(pf, px, fr, {"nifty": -3}, {})
    assert abs(r["pnl_pct"] - (-0.039)) < 0.003
    assert r["by_ticker"][0]["method"] == "ols" and r["judgment_factors"] == []


def test_judgment_factor_and_missing_market_factor_fallback():
    sens = load_json("sector_sensitivity.json")
    assert "Energy" in sens                        # data file is found from the repo root
    pf, px, fr = _setup("FMCG")
    r = scenario.run(pf, px, fr, {"monsoon_rain": -20, "crude": 10}, sens)   # no crude series -> judgment
    row = r["by_ticker"][0]
    assert row["method"] == "judgment" and set(r["judgment_factors"]) == {"monsoon_rain", "crude"}
    assert row["betas"]["monsoon_rain"] > 0 and row["pnl_inr"] < 0            # deficient monsoon hurts FMCG
    assert row["betas"]["crude"] < 0                                         # dearer crude hurts FMCG


def test_mixed_methods():
    sens = load_json("sector_sensitivity.json")
    pf, px, fr = _setup("Energy")
    r = scenario.run(pf, px, fr, {"nifty": -2, "heatwave": 5}, sens)
    assert r["by_ticker"][0]["method"] == "mixed"
