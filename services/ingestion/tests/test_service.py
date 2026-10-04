"""Needs copilot_common installed (pip install -e packages/copilot_common). Skipped otherwise."""
import pytest
pytest.importorskip("copilot_common")
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, env):
    env(DATA_DIR=tmp_path, WORKER="0", MOCK_DELAY_MS="0", MOCK="0", CACHE_MODE="record")
    from services.ingestion.app import app
    with TestClient(app) as c:
        yield c


def test_replay_empty_cache_is_degraded_200_no_network(client, env):
    env(CACHE_MODE="replay")
    for path, body in (("/weather/features", {"region_id": "OD-Puri"}), ("/prices", {"tickers": ["ITC.NS"]}),
                       ("/news", {"query": "cyclone"}), ("/macro/features", {})):
        r = client.post(path, json=body)
        assert r.status_code == 200, path
        assert r.json()["evidence"][0]["degraded"] is True, path


def test_chaos_weather_down(client):
    r = client.post("/weather/features", json={"region_id": "OD-Puri", "chaos": {"weather_down": True}})
    ev = r.json()["evidence"][0]
    assert r.status_code == 200 and ev["degraded"] and ev["degraded_reason"] == "chaos"


def test_mock_mode_returns_fixtures(client, env):
    env(MOCK="1")
    for path, body, tool in (("/weather/features", {"region_id": "OD-Puri"}, "weather"),
                             ("/macro/features", {}, "macro"), ("/news", {}, "news"),
                             ("/prices", {"tickers": ["ONGC.NS"]}, "prices"),
                             ("/announcements", {"tickers": ["ITC.NS"]}, "news")):
        ev = client.post(path, json=body).json()["evidence"][0]
        assert ev["tool"] == tool and ev["degraded_reason"] == "mock"


def test_unknown_region_is_422(client):
    assert client.post("/weather/features", json={"region_id": "XX-Nowhere"}).status_code == 422


def test_regions(client):
    ids = {r["region_id"] for r in client.get("/regions").json()}
    # the 15 original regions, plus the 7 the orchestrator resolves to (added 2026-10-04)
    assert {"OD-Puri", "GJ-Kutch", "MH-Yavatmal", "US-LA-PortFourchon", "GULF-Central"} <= ids
    assert {"MP-Ujjain", "MH-Latur", "KA-Kalaburagi", "RJ-Jodhpur", "RJ-Jaipur", "UP-Lucknow", "DL-NewDelhi"} <= ids
    assert len(ids) == 22


def test_as_of_fallback_never_serves_live_last_good(client):
    """A failed time-machine request must not fall back to today's cached value (look-ahead)."""
    from services.ingestion import store
    from services.ingestion.timeutil import utcnow
    store.save_last_good("weather", "OD-Puri", {"region": "OD-Puri", "rain_anomaly_pct": 99.0}, utcnow(), 0.75)
    ev = client.post("/weather/features", json={"region_id": "OD-Puri", "as_of": "2021-08-25",
                                                "chaos": {"weather_down": True}}).json()["evidence"][0]
    assert ev["degraded"] and "rain_anomaly_pct" not in ev["value"]
    assert ev["as_of"] <= "2021-08-25T23:59:59Z"
    live = client.post("/weather/features", json={"region_id": "OD-Puri", "chaos": {"weather_down": True}}).json()
    assert live["evidence"][0]["value"]["rain_anomaly_pct"] == 99.0


def test_prices_accepts_single_ticker_and_start_end(client, env):
    env(CACHE_MODE="replay")   # no network: just the request shape
    for body in ({"ticker": "ITC.NS", "start": "2021-01-01", "end": "2021-06-30"},
                 {"tickers": ["ITC.NS"], "start": "2021-01-01"}):
        r = client.post("/prices", json=body)
        assert r.status_code == 200 and r.json()["evidence"][0]["value"]["ticker"] == "ITC.NS"
    assert client.post("/prices", json={}).status_code == 422


def test_feed_since_shape(client):
    from services.ingestion import worker
    worker.TICKS.append({"ticker": "ITC.NS", "ts": "2026-10-03T05:00:00Z", "close": 400.0, "volume": 1})
    worker.RING.append({"news_id": "x", "title": "t", "ingested_at": "2026-10-03T05:00:00Z"})
    try:
        d = client.get("/feed/since", params={"ts": "2026-10-03T00:00:00Z"}).json()
        assert len(d["news"]) == 1 and d["ticks"] == d["prices"] and len(d["ticks"]) == 1
        assert client.get("/feed/since").status_code == 200        # monitor may omit ts
    finally:
        worker.TICKS.clear(); worker.RING.clear()
