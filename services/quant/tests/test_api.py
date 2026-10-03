import asyncio
from datetime import date
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from copilot_common.models import ToolResult
from copilot_common.settings import settings
from synthetic import make_market, make_stock, prices_from_returns
import quant.main as qm
from quant import data as qdata

client = TestClient(qm.app)
PF = {"portfolio_id": "demo", "currency": "INR", "cash": 0, "holdings": [
    {"ticker": "RELIANCE.NS", "qty": 100, "sector": "Energy"}, {"ticker": "ITC.NS", "qty": 400, "sector": "FMCG"},
    {"ticker": "HDFCBANK.NS", "qty": 150, "sector": "Banks"}]}


def _frame(tickers):
    bench = make_market(700, seed=61)
    out = {}
    for i, t in enumerate(tickers):
        if t == "^NSEI":
            out[t] = prices_from_returns(bench, 25000, log=False)
        else:
            out[t] = prices_from_returns(make_stock(bench, 0.8 + 0.2 * i, noise=0.006, seed=70 + i), 500, log=False)
    df = pd.DataFrame(out)
    df.index = df.index.normalize()
    return df


@pytest.fixture(autouse=True)
def fake_prices(monkeypatch):
    async def fake(tickers, start, end, as_of=None):
        df = _frame(list(tickers))
        cutoff = min(end, as_of) if as_of else end
        return df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(cutoff))], set()
    monkeypatch.setattr(qm, "get_prices", fake)
    monkeypatch.setattr(settings, "MOCK", 0)


def _check(r):
    assert r.status_code == 200, r.text
    tr = ToolResult.model_validate(r.json())
    assert len(tr.evidence) == 1
    return tr


def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["service"] == "quant"


def test_var_endpoint_validates_and_is_reproducible():
    # as_of falls inside the synthetic history (2023-01-03 .. ~2025-09)
    body = {"portfolio": PF, "n_paths": 10000, "seed": 42, "as_of": "2025-06-30", "lookback_days": 500}
    a = _check(client.post("/var_montecarlo", json=body, headers={"X-Run-Id": "run_x"}))
    b = _check(client.post("/var_montecarlo", json=body))
    ev = a.evidence[0]
    assert ev.tool == "risk" and ev.run_id == "run_x" and ev.id.startswith("ev_risk_")
    assert ev.value["var_inr"] == b.evidence[0].value["var_inr"] and ev.value["var_inr"] > 0
    assert ev.confidence == 0.75 and not ev.degraded
    assert ev.summary.startswith("5d 95% MC VaR ₹")


def test_other_endpoints_validate():
    a = {"as_of": "2025-06-30"}
    assert _check(client.post("/exposure", json={"portfolio": PF, **a})).evidence[0].confidence == 0.8
    s = _check(client.post("/scenario", json={"portfolio": PF, "shocks": {"nifty": -3, "monsoon_rain": -20}, **a}))
    assert s.evidence[0].confidence == 0.4 and any("judgment" in w for w in s.warnings)
    h = _check(client.post("/hedge_proposals", json={"portfolio": PF, "evidence_ids": ["ev_risk_007"], **a}))
    assert h.evidence[0].value["proposals"] and h.evidence[0].confidence == 0.7
    assert h.evidence[0].id in h.evidence[0].value["proposals"][0]["evidence_ids"]
    c = _check(client.post("/correlations", json={"tickers": ["RELIANCE.NS", "ITC.NS", "^NSEI"], **a}))
    assert c.evidence[0].value["pairs"]
    bs = _check(client.post("/bs_price", json={"spot": 25000, "strike": 24000, "vol": 0.17, "days": 20}))
    assert bs.evidence[0].value["delta"] < 0


def test_event_study_endpoint():
    bench = make_market(700, seed=61)
    ev_date = bench.index[400].date().isoformat()
    r = _check(client.post("/event_study", json={"ticker": "RELIANCE.NS", "event_date": ev_date}))
    assert r.evidence[0].tool == "event_study" and "CAR(-1,+5)" in r.evidence[0].summary


def test_invalid_input_is_422_and_data_problems_are_200():
    assert client.post("/scenario", json={"portfolio": PF, "shocks": {"bogus": 1}}).status_code == 422
    assert client.post("/var_montecarlo", json={"portfolio": {"holdings": []}}).status_code == 422
    r = client.post("/var_montecarlo", json={"portfolio": PF, "as_of": "2023-01-05"})   # ~2 rows of history
    assert r.status_code == 200
    ev = ToolResult.model_validate(r.json()).evidence[0]
    assert ev.degraded and ev.degraded_reason


def test_missing_ticker_marks_degraded_and_halves_confidence(monkeypatch):
    async def fake(tickers, start, end, as_of=None):
        df = _frame([t for t in tickers if t != "ITC.NS"])
        return df[df.index <= pd.Timestamp(as_of or end)], {"ITC.NS"}
    monkeypatch.setattr(qm, "get_prices", fake)
    r = _check(client.post("/var_montecarlo", json={"portfolio": PF, "as_of": "2025-06-30"}))
    ev = r.evidence[0]
    assert ev.degraded and ev.degraded_reason == "missing_data" and ev.confidence == 0.25   # 0.5 (partial) x 0.5


def test_mock_mode(monkeypatch):
    monkeypatch.setattr(settings, "MOCK", 1)
    monkeypatch.setattr(settings, "MOCK_DELAY_MS", 0)
    for ep, body in [("var_montecarlo", {"portfolio": PF}), ("scenario", {"portfolio": PF, "shocks": {"crude": 1}}),
                     ("hedge_proposals", {"portfolio": PF}), ("exposure", {"portfolio": PF}),
                     ("event_study", {"ticker": "ONGC.NS", "event_date": "2019-05-03"}),
                     ("correlations", {"tickers": ["A", "B"]}),
                     ("bs_price", {"spot": 1, "strike": 1, "vol": 0.2, "days": 10})]:
        r = client.post(f"/{ep}", json=body)
        ev = ToolResult.model_validate(r.json()).evidence[0]
        assert ev.degraded and ev.degraded_reason == "mock", ep
    assert client.get("/health").json()["mock"] is True


# ---------------- time machine: data layer never returns rows after as_of
def test_get_prices_never_uses_data_after_as_of(monkeypatch):
    full = _frame(["RELIANCE.NS"])

    async def boom(*a, **k):
        raise RuntimeError("ingestion down")

    async def yf(tickers, start, end):                 # misbehaving source: ignores the cutoff entirely
        return {"RELIANCE.NS": full["RELIANCE.NS"]}
    monkeypatch.setattr(qdata, "_fetch_ingest", boom)
    monkeypatch.setattr(qdata, "_fetch_yf", yf)
    as_of = date(2024, 5, 1)
    df, deg = asyncio.run(qdata.get_prices(["RELIANCE.NS"], date(2023, 1, 1), date(2026, 1, 1), as_of))
    assert df.index.max() <= pd.Timestamp(as_of) and len(df) > 100
    assert deg == {"RELIANCE.NS"}                       # fallback path => degraded


def test_get_prices_ingestion_path_not_degraded(monkeypatch):
    full = _frame(["RELIANCE.NS"])

    async def ing(tickers, start, end, as_of):
        return {"RELIANCE.NS": full["RELIANCE.NS"]}, set()
    monkeypatch.setattr(qdata, "_fetch_ingest", ing)
    df, deg = asyncio.run(qdata.get_prices(["RELIANCE.NS"], date(2023, 1, 1), date(2023, 12, 31), None))
    assert deg == set() and df.index.max() <= pd.Timestamp("2023-12-31")
