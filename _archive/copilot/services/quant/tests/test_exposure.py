import numpy as np
import pandas as pd
from copilot_common.models import Holding, Portfolio
from quant import exposure
from quant.data import load_json
from synthetic import make_market, make_stock, prices_from_returns


def test_exposure_weights_and_heatmap():
    bench = make_market(300, seed=51)
    px = pd.DataFrame({
        "RELIANCE.NS": prices_from_returns(make_stock(bench, 1.1, noise=0.004, seed=52), 2800, log=False),
        "ITC.NS": prices_from_returns(make_stock(bench, 0.7, noise=0.004, seed=53), 420, log=False),
        "HDFCBANK.NS": prices_from_returns(make_stock(bench, 1.0, noise=0.004, seed=54), 1600, log=False)})
    b = prices_from_returns(bench, 25000, log=False)
    pf = Portfolio(holdings=[Holding(ticker="RELIANCE.NS", qty=100, sector="Energy"),
                             Holding(ticker="ITC.NS", qty=400, sector="FMCG"),
                             Holding(ticker="HDFCBANK.NS", qty=150, sector="Banks")])
    sens = load_json("sector_sensitivity.json")
    r = exposure.run(pf, px, b, sens)
    assert abs(sum(t["weight"] for t in r["by_ticker"]) - 1) < 1e-3
    assert abs(sum(r["by_sector"].values()) - 1) < 1e-3
    hm = r["heatmap"]
    assert hm["cols"] == ["weather", "agri", "crude", "rates", "usd_inr"]
    assert np.array(hm["values"]).shape == (3, 5)
    i = hm["rows"].index("Energy")
    assert abs(hm["values"][i][0] - r["by_sector"]["Energy"] * 0.6) < 1e-3     # weight x sensitivity
    betas = {t["ticker"]: t["beta"] for t in r["by_ticker"]}
    assert abs(betas["RELIANCE.NS"] - 1.1) < 0.15 and abs(betas["ITC.NS"] - 0.7) < 0.15


def test_unknown_sector_uses_other():
    sens = load_json("sector_sensitivity.json")
    assert exposure.sector_sens(sens, "Nope") == sens["Other"]
    assert exposure.sector_sens(sens, "_meta") == sens["Other"]
