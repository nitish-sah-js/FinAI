"""Endpoints: ToolResult contract, X-Run-Id evidence ids, degraded paths, MOCK fixture, consumer compatibility."""
from datetime import datetime, timezone

import pytest

from copilot_common.ids import new_run_id
from copilot_common.models import ToolResult
from copilot_common.service_base import load_fixture
from copilot_common.settings import reload_settings

ORCH_BODY = {"region_id": "MH-Yavatmal", "date": "2026-10-03", "crop_season": None, "as_of": None,
             "run_id": None, "chaos": {"weather_down": False, "force_rate_limit": False,
                                       "agri_raster_missing": False, "vector_down": False, "slow_network_ms": 0}}


def _tr(resp) -> ToolResult:
    assert resp.status_code == 200, resp.text
    return ToolResult.model_validate(resp.json())


def test_orchestrator_body_and_run_id_header(client):
    rid = new_run_id()
    tr = _tr(client.post("/agri_signal", json={**ORCH_BODY, "run_id": rid}, headers={"X-Run-Id": rid}))
    ev = tr.evidence[0]
    assert ev.id == "ev_agri_001" and ev.run_id == rid and ev.tool == "agri"
    tr2 = _tr(client.post("/agri_signal", json={**ORCH_BODY, "run_id": rid}, headers={"X-Run-Id": rid}))
    assert tr2.evidence[0].id == "ev_agri_002"               # per-run counter
    v = ev.value
    assert list(v["class_probs"]) == ["healthy", "watch", "stressed", "severe"]
    assert v["stress_class"] in v["class_probs"] and v["region_id"] == "MH-Yavatmal"
    assert v["yield_anomaly_pct"] is None
    assert any("yield_anomaly_pct is null" in w for w in tr.warnings)
    assert all({"ticker", "link", "sign", "strength"} <= set(e) for e in v["linked_equities"])
    assert set(v["trend"]) >= {"ndvi_anomaly_z_prev", "direction"}
    assert 0 <= ev.confidence <= 1 and ev.model_version == v["model_version"]
    assert ev.freshness_s == int((ev.timestamp - ev.as_of).total_seconds())


def test_monitor_body_with_nulls(client):
    tr = _tr(client.post("/agri_signal", json={"region_id": "GJ-Rajkot", "date": None, "crop_season": None,
                                               "as_of": None}))
    assert tr.evidence[0].value["stress_class"] in ("healthy", "watch", "stressed", "severe")


def test_current_request_is_stale_degraded(client, store):
    tr = _tr(client.post("/agri_signal", json={"region_id": "MP-Indore"}))
    ev = tr.evidence[0]
    assert ev.degraded and "stale_imagery" in ev.degraded_reason
    assert ev.value["period_end"] == str(store.latest_period_end.date())
    assert ev.value["imagery_age_days"] > 40
    assert ev.freshness_s > 40 * 86400
    p = max(ev.value["class_probs"].values())
    assert ev.confidence <= round(p * 0.5, 3) + 1e-9         # degraded halves confidence


def test_time_machine_as_of(client):
    tr = _tr(client.post("/agri_signal", json={"region_id": "MH-Latur", "date": "2015-09-20", "as_of": "2015-09-20"}))
    ev = tr.evidence[0]
    assert ev.as_of <= datetime(2015, 9, 20, 23, 59, 59, tzinfo=timezone.utc)
    assert ev.value["period_end"] == "2015-09-13"
    assert not ev.degraded                                    # 7 days old: fresh in time-machine mode
    assert ev.value["baseline_years"] == "2011-2014"
    assert any("baseline year" in w for w in tr.warnings)


def test_as_of_with_later_date_uses_as_of(client):
    tr = _tr(client.post("/agri_signal", json={"region_id": "MH-Latur", "date": "2026-10-03", "as_of": "2015-09-20"}))
    assert tr.evidence[0].value["period_end"] <= "2015-09-20"


def test_unknown_region_is_degraded_200(client, store):
    rid = new_run_id()
    tr = _tr(client.post("/agri_signal", json={"region_id": "OD-Cuttack"}, headers={"X-Run-Id": rid}))
    ev = tr.evidence[0]
    assert ev.degraded and ev.degraded_reason == "unknown_region" and ev.id == "ev_agri_001"
    assert ev.value["stress_class"] is None and ev.value["yield_anomaly_pct"] is None
    assert all(r in tr.warnings[0] for r in store.regions)


def test_region_case_insensitive(client):
    tr = _tr(client.post("/agri_signal", json={"region_id": "pb-ludhiana"}))
    assert tr.evidence[0].value["region_id"] == "PB-Ludhiana"


def test_before_archive_degraded(client):
    tr = _tr(client.post("/agri_signal", json={"region_id": "MH-Latur", "as_of": "2010-06-01"}))
    assert tr.evidence[0].degraded_reason == "no_imagery_before_cutoff"


@pytest.mark.parametrize("how", ["body", "header"])
def test_chaos_raster_missing(client, how):
    body = {"region_id": "MH-Yavatmal", "as_of": "2019-09-20"}
    normal = _tr(client.post("/agri_signal", json=body)).evidence[0]
    if how == "body":
        resp = client.post("/agri_signal", json={**body, "chaos": {"agri_raster_missing": True}})
    else:
        resp = client.post("/agri_signal", json=body, headers={"X-Chaos": "weather_down,agri_raster_missing"})
    ev = _tr(resp).evidence[0]
    assert ev.degraded and "raster_missing_last_cached" in ev.degraded_reason
    assert ev.value["period_end"] < normal.value["period_end"]


def test_bad_date_is_422(client):
    assert client.post("/agri_signal", json={"region_id": "MH-Latur", "date": "not-a-date"}).status_code == 422


def test_batch_by_tickers_and_ids(client):
    rid = new_run_id()
    tr = _tr(client.post("/agri_signal/batch", json={"tickers": ["UPL.NS", "ADANIPORTS.NS", "TCS.NS"]},
                         headers={"X-Run-Id": rid}))
    regions = [e.value["region_id"] for e in tr.evidence]
    assert set(regions) == {"MH-Yavatmal", "MH-Latur", "MP-Indore", "GJ-Rajkot"}
    assert [e.id for e in tr.evidence] == [f"ev_agri_{i:03d}" for i in range(1, len(regions) + 1)]
    assert any("OD-Cuttack" in w or "no agri model" in w for w in tr.warnings)    # UPL also maps to OD-Cuttack
    assert any("TCS.NS" in w for w in tr.warnings)
    tr = _tr(client.post("/agri_signal/batch", json={"region_ids": ["PB-Ludhiana", "XX-Nowhere"]}))
    assert [e.degraded_reason for e in tr.evidence][1] == "unknown_region"
    tr = _tr(client.post("/agri_signal/batch", json={}))
    assert len(tr.evidence) == 8


def test_regions_model_info_health(client):
    r = client.get("/regions").json()
    assert len(r["regions"]) == 8 and all(x["lat"] and x["lon"] for x in r["regions"])
    mi = client.get("/model_info").json()
    assert {"model_accuracy", "persistence_accuracy", "persistence_macro_f1", "beats_persistence"} <= set(mi["cv"])
    assert "persistence" in mi["verdict"] and mi["cv_folds"]
    h = client.get("/health").json()
    assert h["service"] == "agri" and h["deps"]["model"] == "ok"


def test_scenario_builder_tolerates_null_yield(client):
    """services/quant/quant/scenario_builder.py logic: null yield -> no agri_stress shock, no crash."""
    ev = _tr(client.post("/agri_signal", json={"region_id": "MH-Yavatmal"})).evidence[0].model_dump(mode="json")
    y = (ev.get("value") or {}).get("yield_anomaly_pct") or {}
    assert not all(k in y and y[k] is not None for k in ("q10", "q50", "q90"))


def test_mock_fixture(client, monkeypatch):
    monkeypatch.setenv("MOCK", "1")
    monkeypatch.setenv("MOCK_DELAY_MS", "0")
    reload_settings()
    try:
        for ep, body in (("/agri_signal", {"region_id": "MH-Yavatmal"}), ("/agri_signal/batch", {"tickers": ["UPL.NS"]})):
            tr = _tr(client.post(ep, json=body))
            assert tr.evidence and all(e.degraded_reason == "mock" for e in tr.evidence)
        for name in ("agri_signal", "agri_signal_batch"):
            ToolResult.model_validate(load_fixture("agri", name))
    finally:
        monkeypatch.setenv("MOCK", "0")
        reload_settings()


def test_fixture_keys_match_real_output(client):
    real = _tr(client.post("/agri_signal", json={"region_id": "MH-Yavatmal"})).evidence[0].value
    fx = load_fixture("agri", "/agri_signal")["evidence"][0]["value"]
    assert set(real) <= set(fx) and set(fx) - set(real) <= {"note"}
    assert set(real["features"]) == set(fx["features"]) and set(real["trend"]) == set(fx["trend"])
