"""Hedge-review fixes: evidence→shock builder (phase 1), beta shrinkage, out-of-sample hedge validation (phase 2),
gated ridge optimizer (phase 3), and the two new endpoints."""
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from copilot_common.models import ToolResult
from copilot_common.settings import settings
from synthetic import make_market, make_stock, prices_from_returns
import quant.main as qm
from quant import scenario, scenario_builder, validation

client = TestClient(qm.app)
PF = {"portfolio_id": "demo", "currency": "INR", "cash": 0, "holdings": [
    {"ticker": "RELIANCE.NS", "qty": 100, "sector": "Energy"}, {"ticker": "HDFCBANK.NS", "qty": 150, "sector": "Banks"}]}
EVID = [
    {"id": "ev_analogs_001", "tool": "analogs", "value": {"distribution": [
        {"asset": "^NSEI", "horizon": "5d", "p10": -0.04, "median": -0.01, "p90": 0.02, "n": 7},
        {"asset": "^NSEI", "horizon": "20d", "p10": -0.09, "median": -0.02, "p90": 0.05, "n": 7}]}},
    {"id": "ev_macro_001", "tool": "macro", "value": {"brent_chg_5d": 0.06, "usd_inr_chg_5d": 0.0, "repo_change_bps": 25}},
    {"id": "ev_agri_001", "tool": "agri", "value": {"yield_anomaly_pct": {"q10": -12, "q50": -4, "q90": 3}}},
    {"id": "ev_weather_001", "tool": "weather", "value": {"storm": {"name": "X"}, "rain_anomaly_pct": 300}},
]
EVENTS = [{"event_id": f"e{i}", "event_date": d} for i, d in
          enumerate(["2024-03-01", "2024-06-03", "2024-09-02", "2024-12-02", "2025-03-03", "2025-06-02"])]


def _frame(tickers, seed=61):
    bench = make_market(700, seed=seed)
    out = {}
    for i, t in enumerate(tickers):
        out[t] = (prices_from_returns(bench, 25000, log=False) if t == "^NSEI" else
                  prices_from_returns(make_stock(bench, 0.9 + 0.1 * i, noise=0.004, seed=80 + i), 500, log=False))
    df = pd.DataFrame(out)
    df.index = df.index.normalize()
    return df


@pytest.fixture
def fake_prices(monkeypatch):
    async def fake(tickers, start, end, as_of=None):
        df = _frame(list(tickers))
        cutoff = min(end, as_of) if as_of else end
        return df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(cutoff))], set()
    monkeypatch.setattr(qm, "get_prices", fake)
    monkeypatch.setattr(settings, "MOCK", 0)


# ---------------------------------------------------------------- phase 1: builder
def test_builder_maps_evidence_to_pp_shocks_with_provenance():
    cases, prov = scenario_builder.build(EVID, "5d")
    assert cases["p10"]["nifty"] == -4.0 and cases["median"]["nifty"] == -1.0 and cases["p90"]["nifty"] == 2.0  # ×100, 5d only
    assert cases["median"]["crude"] == 6.0             # macro "recent move persists" (no analog covers crude)
    assert "usd_inr" not in cases["median"]           # zero move → no shock
    assert cases["median"]["repo_bps"] == 25
    assert cases["p10"]["agri_stress"] == 12 and cases["p90"]["agri_stress"] == -3   # stress = −yield
    assert "monsoon_rain" not in cases["median"]      # storm rain is not a monsoon signal
    src = {p["factor"]: p["source_evidence"] for p in prov}
    assert src == {"nifty": "ev_analogs_001", "crude": "ev_macro_001", "repo_bps": "ev_macro_001", "agri_stress": "ev_agri_001"}


def test_builder_clips_and_analog_beats_macro():
    ev = [{"id": "ev_analogs_001", "tool": "analogs", "value": {"distribution": [
              {"asset": "CL=F", "horizon": "5d", "n": 5, "p10": -0.9, "median": 0.01, "p90": 0.9}]}},
          {"id": "ev_macro_001", "tool": "macro", "value": {"brent_chg_5d": 0.2}},
          {"id": "ev_weather_001", "tool": "weather", "value": {"storm": None, "rain_anomaly_pct": -80}}]
    cases, prov = scenario_builder.build(ev)
    assert cases["p10"]["crude"] == -40.0 and cases["p90"]["crude"] == 40.0 and cases["median"]["crude"] == 1.0
    assert cases["median"]["monsoon_rain"] == -40.0
    assert [p["source_evidence"] for p in prov if p["factor"] == "crude"] == ["ev_analogs_001"]


def test_builder_empty_evidence():
    cases, prov = scenario_builder.build([{"id": "ev_news_001", "tool": "news", "value": {}}])
    assert cases == {"p10": {}, "median": {}, "p90": {}} and prov == []


# ---------------------------------------------------------------- beta shrinkage
def test_shrinkage_keeps_significant_and_pulls_noise_to_prior():
    rng = np.random.default_rng(0)
    x = pd.Series(rng.normal(0, 0.01, 250))
    strong = scenario.ols_fit(1.2 * x + pd.Series(rng.normal(0, 0.002, 250)), x)
    noise = scenario.ols_fit(pd.Series(rng.normal(0, 0.01, 250)), x)
    b, w = scenario.shrink(strong, prior=0.0)
    assert w > 0.99 and abs(b - 1.2) < 0.05
    b2, w2 = scenario.shrink(noise, prior=0.3)
    assert w2 < 0.5 and abs(b2 - 0.3) < abs(noise["beta"] - 0.3)


# ---------------------------------------------------------------- phase 2: validation
def test_validation_reduces_drawdown_and_reports_n_and_range():
    df = _frame(["RELIANCE.NS", "HDFCBANK.NS", "^NSEI"])
    res = validation.validate(df, {"RELIANCE.NS": 100, "HDFCBANK.NS": 150}, "^NSEI", EVENTS, compare_optimizer=False)
    assert res["n"] == 6 and res["skipped"] == []
    assert res["n_improved"] >= 5 and res["median_dd_reduction_pp"] > 0
    assert res["range_kind"].startswith("p10") and len(res["range_dd_reduction_pp"]) == 2
    assert all(0.7 < r["hedge_ratio"] < 1.4 for r in res["events"])


def test_validation_has_no_lookahead():
    df = _frame(["RELIANCE.NS", "^NSEI"])
    ev = [{"event_id": "a", "event_date": "2024-06-03"}]
    base = validation.validate(df, {"RELIANCE.NS": 100}, "^NSEI", ev, compare_optimizer=False)["events"][0]
    wild = df.copy()
    after = wild.index >= pd.Timestamp("2024-06-03")
    wild.loc[after, "RELIANCE.NS"] *= np.linspace(1, 3, after.sum())    # rewrite the future
    res = validation.validate(wild, {"RELIANCE.NS": 100}, "^NSEI", ev, compare_optimizer=False)["events"][0]
    assert res["hedge_ratio"] == base["hedge_ratio"]                    # fitted strictly before the event
    assert res["ret_unhedged"] != base["ret_unhedged"]                  # …while the forward path did change


def test_validation_skips_events_without_forward_data():
    df = _frame(["RELIANCE.NS", "^NSEI"])
    last = df.index[-2].date().isoformat()
    res = validation.validate(df, {"RELIANCE.NS": 100}, "^NSEI", [{"event_id": "late", "event_date": last}],
                              compare_optimizer=False)
    assert res["n"] == 0 and "late" in res["skipped"][0]


# ---------------------------------------------------------------- phase 3: gated optimizer
def test_optimizer_not_adopted_with_small_n():
    df = _frame(["RELIANCE.NS", "^NSEI", "^NSEBANK", "INR=X"])
    res = validation.validate(df, {"RELIANCE.NS": 100}, "^NSEI", EVENTS[:3])
    o = res["optimizer"]
    assert o["n"] == 3 and o["adopt"] is False and "only 3 events" in o["decision"]
    assert all(abs(w) <= validation.INSTRUMENTS[j]["cap"] for e in o["events"] for j, w in e["weights"].items())


def test_ridge_shrinks_weights():
    rng = np.random.default_rng(1)
    X = pd.DataFrame({"^NSEI": rng.normal(0, 0.01, 250), "^NSEBANK": rng.normal(0, 0.01, 250)})
    X["^NSEBANK"] = 0.95 * X["^NSEI"] + 0.05 * X["^NSEBANK"]               # near-collinear pair
    rp = 1.0 * X["^NSEI"] + pd.Series(rng.normal(0, 0.003, 250))
    caps = {"^NSEI": 5, "^NSEBANK": 5}
    loose = validation.ridge_weights(rp, X, 0.0, caps)
    tight = validation.ridge_weights(rp, X, 1.0, caps)
    assert sum(abs(v) for v in tight.values()) < sum(abs(v) for v in loose.values())


# ---------------------------------------------------------------- endpoints
def test_scenario_from_evidence_endpoint(fake_prices):
    r = client.post("/scenario/from_evidence", json={"portfolio": PF, "evidence": EVID, "as_of": "2025-06-30"},
                    headers={"X-Run-Id": "run_r1"})
    assert r.status_code == 200, r.text
    ev = ToolResult.model_validate(r.json()).evidence[0]
    assert ev.tool == "scenario" and ev.id.startswith("ev_scenario_") and not ev.degraded
    v = ev.value
    assert set(v["cases"]) == {"p10", "median", "p90"} and v["worst_case"] in v["cases"]
    assert v["pnl_inr"] == v["cases"]["median"]["pnl_inr"]              # top level keeps the /scenario contract
    assert v["cases"]["p10"]["pnl_inr"] < v["cases"]["p90"]["pnl_inr"]
    assert {p["factor"] for p in v["shock_provenance"]} >= {"nifty", "agri_stress"}


def test_scenario_from_evidence_degrades_without_shocks(fake_prices):
    r = client.post("/scenario/from_evidence", json={"portfolio": PF, "evidence": []})
    ev = ToolResult.model_validate(r.json()).evidence[0]
    assert ev.degraded and "no factor shocks" in ev.degraded_reason


def test_hedge_validation_endpoint(fake_prices):
    r = client.post("/hedge_validation", json={"portfolio": PF, "events": EVENTS, "as_of": "2025-09-01"},
                    headers={"X-Run-Id": "run_r2"})
    assert r.status_code == 200, r.text
    ev = ToolResult.model_validate(r.json()).evidence[0]
    assert ev.tool == "hedge_validation" and ev.id == "ev_hedge_validation_001" and ev.run_id == "run_r2"
    assert ev.value["n"] == 6 and ev.confidence <= 0.5 and "6 past events" in ev.summary
    assert "optimizer" in ev.value and ev.value["optimizer"]["decision"]


def test_hedge_validation_bad_events(fake_prices):
    r = client.post("/hedge_validation", json={"portfolio": PF, "events": [{"event_id": "x", "event_date": "soon"}]})
    ev = ToolResult.model_validate(r.json()).evidence[0]
    assert ev.degraded


@pytest.mark.parametrize("url,body", [("/scenario/from_evidence", {"portfolio": PF, "evidence": EVID}),
                                      ("/hedge_validation", {"portfolio": PF, "events": EVENTS})])
def test_mock_fixtures(monkeypatch, url, body):
    monkeypatch.setattr(settings, "MOCK", 1)
    monkeypatch.setattr(settings, "MOCK_DELAY_MS", 0, raising=False)
    tr = ToolResult.model_validate(client.post(url, json=body).json())
    assert tr.evidence and tr.evidence[0].degraded          # mock is always marked


def test_scenario_from_degraded_inputs_is_degraded(fake_prices):
    evid = [dict(EVID[1], degraded=True, degraded_reason="service_unreachable")]       # macro from a fixture
    r = client.post("/scenario/from_evidence", json={"portfolio": PF, "evidence": evid, "as_of": "2025-06-30"})
    tr = ToolResult.model_validate(r.json())
    assert tr.evidence[0].degraded and "ev_macro_001" in tr.evidence[0].degraded_reason


def test_builder_ignores_analog_distribution_from_too_few_events():
    ev = [{"id": "ev_analogs_001", "tool": "analogs", "value": {"distribution": [
        {"asset": "^NSEI", "horizon": "5d", "measure": "raw", "n": 1, "p10": -0.033, "median": -0.033, "p90": -0.033}]}}]
    cases, prov = scenario_builder.build(ev)
    assert "nifty" not in cases["median"] and prov == []


def test_validation_when_the_index_is_also_held():
    df = _frame(["RELIANCE.NS", "^NSEI"])
    res = validation.validate(df, {"RELIANCE.NS": 100, "^NSEI": 5}, "^NSEI", EVENTS, compare_optimizer=False)
    assert res["n"] == 6 and res["skipped"] == []
