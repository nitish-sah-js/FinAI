"""HTTP layer: ToolResult contract, X-Run-Id evidence ids, orchestrator request body, MOCK fixtures, fallback latency,
news endpoints with Weaviate down."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from copilot_common.models import ToolResult
from copilot_common.service_base import load_fixture
from copilot_common.settings import reload_settings

from vectordb import news as news_mod
from vectordb.main import app

client = TestClient(app)

ORCH_BODY = {  # exactly what services/orchestrator/tools_client.analogs + call_tool send
    "situation": "cyclone Odisha. Severe cyclone approaching the Odisha coast. Impact on my portfolio over 5 days?",
    "event_type": "cyclone", "region_hint": "Odisha", "assets": ["ITC.NS", "COALINDIA.NS", "^NSEI"],
    "horizon": "5d", "k": 5, "alpha": 0.6, "exclude_holdout": False, "as_of": None,
    "run_id": "run_20261003141502_a91f",
    "chaos": {"weather_down": False, "force_rate_limit": False, "agri_raster_missing": False, "vector_down": False,
              "slow_network_ms": 0},
}


def test_orchestrator_body_validates_and_ids_follow_x_run_id(embedder):
    rid = "run_test_ids_0001"
    bodies = [client.post("/find_analogs", json=ORCH_BODY, headers={"X-Run-Id": rid}).json() for _ in range(2)]
    ids = []
    for b in bodies:
        tr = ToolResult.model_validate(b)
        ev = tr.evidence[0]
        assert ev.tool == "analogs" and ev.run_id == rid and ev.degraded
        assert ev.degraded_reason == "weaviate_down_numpy_fallback"
        ids.append(ev.id)
        v = ev.value
        assert isinstance(v["analogs"], list) and isinstance(v["distribution"], list)
        assert isinstance(v["filters_relaxed"], list) and v["confidence"] in ("low", "medium", "high")
        for a in v["analogs"]:
            assert {"event_id", "title", "event_date", "similarity", "why_similar"} <= a.keys()
    assert ids == ["ev_analogs_001", "ev_analogs_002"]
    # body run_id is used when the header is absent
    b = client.post("/find_analogs", json={**ORCH_BODY, "run_id": "run_body_only"}).json()
    assert b["evidence"][0]["id"] == "ev_analogs_001" and b["evidence"][0]["run_id"] == "run_body_only"


def test_degraded_confidence_is_reduced(embedder):
    from vectordb.stats import confidence_label
    ev = client.post("/find_analogs", json=ORCH_BODY).json()["evidence"][0]
    sims = [a["similarity"] for a in ev["value"]["analogs"]]
    _, base = confidence_label(len(sims), float(np.mean(sims)), len(ev["value"]["filters_relaxed"]))
    assert ev["confidence"] == pytest.approx(round(base - 0.1, 3))


def test_as_of_evidence_not_after_cutoff(embedder):
    body = {**ORCH_BODY, "as_of": "2023-06-14", "exclude_holdout": True}
    ev = client.post("/find_analogs", json=body).json()["evidence"][0]
    assert ev["as_of"][:10] <= "2023-06-14"
    assert all(a["event_date"] < "2023-06-14" for a in ev["value"]["analogs"])


def test_chaos_vector_down_uses_fallback(embedder):
    body = {**ORCH_BODY, "chaos": {**ORCH_BODY["chaos"], "vector_down": True}}
    ev = client.post("/find_analogs", json=body).json()["evidence"][0]
    assert ev["degraded"] and ev["degraded_reason"] == "chaos"


def test_fallback_latency_warm_under_300ms(embedder):
    client.post("/find_analogs", json=ORCH_BODY)           # warm (model + corpus vectors)
    lat = []
    for _ in range(15):
        t = time.perf_counter()
        r = client.post("/find_analogs", json=ORCH_BODY)
        lat.append((time.perf_counter() - t) * 1000)
        assert r.status_code == 200
    p95 = float(np.percentile(lat, 95))
    assert p95 < 300, f"fallback p95 {p95:.0f} ms"


def test_health_reports_weaviate_down_fast(embedder):
    t = time.perf_counter()
    h = client.get("/health").json()
    assert (time.perf_counter() - t) < 0.5
    assert h["deps"]["weaviate"] == "down" and h["deps"]["embedder"] == "ok" and h["status"] == "degraded"


def test_embed_and_latency_endpoints(embedder):
    r = client.post("/embed", json={"texts": ["a", "b"]}).json()
    assert len(r["vectors"]) == 2 and len(r["vectors"][0]) == 384 and r["model"] == "BAAI/bge-small-en-v1.5"
    lat = client.get("/latency").json()
    assert {"pipeline_ms", "end_to_end_s"} <= lat.keys()


# ---------------------------------------------------------------- news with Weaviate down
def _news(i: int, **kw) -> dict:
    now = datetime.now(timezone.utc)
    return {"news_id": f"n{i}", "title": f"Cyclone nears Odisha coast, ports shut {i}",
            "summary": "IMD warns of very severe cyclone", "source": "test", "url": f"https://x/{i}",
            "published_at": (now - timedelta(minutes=10)).isoformat(), "tickers": ["^NSEI"],
            "ingested_at": (now - timedelta(milliseconds=200)).isoformat(), **kw}


def test_news_index_and_search_degraded_not_error(embedder):
    news_mod.clear_memory()
    items = [_news(1, sentiment_label=None, sentiment_score=None), _news(2, sentiment_label="negative",
                                                                          sentiment_score=-0.6), _news(1)]
    tr = ToolResult.model_validate(client.post("/news/index", json={"items": items}, headers={"X-Run-Id": "run_n"}).json())
    ev = tr.evidence[0]
    assert ev.tool == "news" and ev.degraded and ev.degraded_reason == "weaviate_down_memory_fallback"
    assert ev.id == "ev_news_001" and ev.run_id == "run_n"
    assert ev.value["indexed"] == 2 and ev.value["skipped_duplicates"] == 1          # batch dedupe
    assert ev.value["pipeline_ms"]["n"] == 2 and ev.value["pipeline_ms"]["p95"] >= 0
    again = client.post("/news/index", json={"items": items[:1]}).json()["evidence"][0]
    assert again["value"]["indexed"] == 0 and again["value"]["skipped_duplicates"] == 1
    s = ToolResult.model_validate(client.post("/news/search", json={"query": "cyclone Odisha ports", "tickers": ["^NSEI"]}).json())
    sev = s.evidence[0]
    assert sev.tool == "news" and sev.degraded and sev.value["n"] == 2
    got = {i["news_id"]: i for i in sev.value["items"]}
    assert got["n1"]["sentiment_label"] is None and got["n1"]["sentiment_score"] is None   # None kept as None
    assert got["n2"]["sentiment_score"] == -0.6
    assert got["n1"]["indexed_at"] >= got["n1"]["ingested_at"]
    none = client.post("/news/search", json={"query": "cyclone", "tickers": ["RELIANCE.NS"]}).json()["evidence"][0]
    assert none["value"]["n"] == 0


# ---------------------------------------------------------------- MOCK fixtures for every endpoint
@pytest.fixture
def mock_mode(monkeypatch):
    monkeypatch.setenv("MOCK", "1")
    monkeypatch.setenv("MOCK_DELAY_MS", "0")
    reload_settings()
    yield
    monkeypatch.delenv("MOCK")
    monkeypatch.delenv("MOCK_DELAY_MS")
    reload_settings()


@pytest.mark.parametrize("endpoint,body", [
    ("/find_analogs", {"situation": "test query", "assets": ["^NSEI"]}),
    ("/news/index", {"items": [_news(9)]}),
    ("/news/search", {"query": "cyclone"}),
])
def test_mock_mode_endpoint(mock_mode, endpoint, body):
    r = client.post(endpoint, json=body)
    assert r.status_code == 200
    tr = ToolResult.model_validate(r.json())
    assert tr.evidence and all(e.degraded and e.degraded_reason == "mock" for e in tr.evidence)
    fx = ToolResult.model_validate(load_fixture("vectordb", endpoint))
    assert fx.evidence[0].tool == ("analogs" if endpoint == "/find_analogs" else "news")


def test_mock_embed(mock_mode):
    assert client.post("/embed", json={"texts": ["x"]}).json()["model"] == "mock"
