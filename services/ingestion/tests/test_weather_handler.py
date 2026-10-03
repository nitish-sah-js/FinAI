"""Weather handler end-to-end with faked Open-Meteo (no network). Needs copilot_common."""
import json, pytest
pytest.importorskip("copilot_common")
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient


def _fake(monkeypatch, heavy=True):
    from services.ingestion.sources import openmeteo as om, storms
    days = pd.date_range("2026-10-03", periods=7).strftime("%Y-%m-%d").tolist()
    precip = [12, 88, 60, 30, 20, 5, 1] if heavy else [1] * 7
    fc = {"daily": {"time": days, "precipitation_sum": precip, "temperature_2m_max": [31.4] * 7,
                    "relative_humidity_2m_mean": [50] * 7, "wind_speed_10m_max": [40] * 7},
          "hourly": {k: [0.1, 0.2, 0.3] * 8 + [None] * 144 for k in om.SOIL_VARS["forecast"]}}

    async def forecast(lat, lon, d=7): return fc
    async def clim(lat, lon, y0=2001, y1=2025, soil_y0=2011):
        idx = pd.date_range(f"{y0}-01-01", f"{y1}-12-31")
        sidx = pd.date_range(f"{soil_y0}-01-01", f"{y1}-12-31")
        rng = np.random.default_rng(0)
        return {"dates": [d.strftime("%Y-%m-%d") for d in idx], "precip": [2.0] * len(idx),
                "soil_dates": [d.strftime("%Y-%m-%d") for d in sidx],
                "soil": [round(float(x), 4) for x in rng.normal(0.25, 0.05, len(sidx))]}
    async def nhc(): return []
    monkeypatch.setattr(om, "forecast", forecast)
    monkeypatch.setattr(om, "climatology", clim)
    monkeypatch.setattr(storms, "nhc_current", nhc)


def test_weather_cyclone_demo(tmp_path, monkeypatch, env):
    env(DATA_DIR=tmp_path, WORKER="0", MOCK="0", CACHE_MODE="record")
    (tmp_path / "storms.json").write_text(json.dumps([{
        "name": "DEMO-ODISHA", "basin": "NIO", "category": "VSCS", "lat": 18.9, "lon": 87.2, "max_wind_kt": 75,
        "track_toward": ["OD-Puri"], "source": "IMD (manual)"}]))
    _fake(monkeypatch)
    from services.ingestion.app import app
    with TestClient(app) as c:
        r = c.post("/weather/features", json={"region_id": "OD-Puri", "horizon_days": 5}).json()
    ev = r["evidence"][0]; v = ev["value"]
    assert not ev["degraded"], r
    assert v["rain_anomaly_pct"] > 100 and v["alerts"] == ["cyclone", "heavy_rain"]
    assert v["storm"]["name"] == "DEMO-ODISHA" and v["soil_moisture_z"] is not None
    assert ev["confidence"] == 0.75 and v["lookahead_risk"] is False
    for k in ("region", "lat", "lon", "rain_anomaly_pct", "heat_index_c", "max_temp_c",
              "soil_moisture_0_7cm", "storm", "alerts", "horizon_days"):
        assert k in v
    # chaos now serves the last good value, confidence x0.5
    with TestClient(app) as c:
        d = c.post("/weather/features", json={"region_id": "OD-Puri", "chaos": {"weather_down": True}}).json()["evidence"][0]
    assert d["degraded"] and d["confidence"] == 0.38 and d["value"]["rain_anomaly_pct"] == v["rain_anomaly_pct"]
