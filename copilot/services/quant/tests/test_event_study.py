import pandas as pd
from quant import event_study
from synthetic import make_market, make_stock, inject_event, prices_from_returns


def test_event_study_recovers_beta_and_car():
    bench = make_market(500, seed=11)
    stock = inject_event(make_stock(bench, beta=1.3, noise=0.002, seed=12), day=300, jump=0.05)
    px, bx = prices_from_returns(stock, name="S"), prices_from_returns(bench, start=1000.0, name="B")
    ev_date = bench.index[300].date()
    r = event_study.run(px, bx, ev_date, (-120, -11), (-1, 5))
    assert abs(r["beta"] - 1.3) < 0.1
    assert abs(r["car"] - 0.05) < 0.01
    assert r["p_value"] < 0.05
    assert [x["d"] for x in r["ar_series"]] == list(range(-1, 6))
    assert abs(r["ar_series"][1]["ar"] - 0.05) < 0.01          # day 0 carries the jump


def test_no_event_is_not_significant():
    bench = make_market(500, seed=21)
    stock = make_stock(bench, beta=1.0, noise=0.004, seed=22)
    r = event_study.run(prices_from_returns(stock), prices_from_returns(bench, 1000.0), bench.index[300].date())
    assert abs(r["car"]) < 0.04


def test_short_history_raises():
    import pytest
    from quant.data import DataError
    bench = make_market(60, seed=1); stock = make_stock(bench)
    with pytest.raises(DataError):
        event_study.run(prices_from_returns(stock), prices_from_returns(bench), bench.index[30].date())
