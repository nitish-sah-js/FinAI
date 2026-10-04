"""HTTP + WebSocket API (04 §8) with MOCK=1."""
import json

from fastapi.testclient import TestClient

Q = "Cyclone heading to Odisha — what happens to my portfolio this week?"


def client():
    from orchestrator.app import app
    return TestClient(app)


def test_query_then_websocket_stream_to_final():
    with client() as c:
        acc = c.post("/query", json={"query": Q}).json()
        assert acc["ws_url"].endswith(f"/ws/{acc['run_id']}")
        msgs = []
        with c.websocket_connect(f"/ws/{acc['run_id']}") as ws:
            while True:
                m = ws.receive_json()
                msgs.append(m)
                if m["type"] == "final":
                    break
        assert msgs[0]["data"]["node"] == "parse_intent"
        seqs = [m["data"]["seq"] for m in msgs if m["type"] == "event"]
        assert seqs == sorted(seqs)
        final = msgs[-1]["data"]
        assert final["run_id"] == acc["run_id"] and final["validator"]["action"] == "pass"

        # late subscriber gets the full replay
        with c.websocket_connect(f"/ws/{acc['run_id']}") as ws:
            replay = []
            while True:
                m = ws.receive_json()
                replay.append(m)
                if m["type"] == "final":
                    break
        assert len(replay) == len(msgs)

        runs = c.get("/runs").json()
        assert runs[0]["run_id"] == acc["run_id"] and runs[0]["status"] == "done"
        detail = c.get(f"/runs/{acc['run_id']}").json()
        assert detail["final"]["query"] == Q and len(detail["events"]) >= 14

        lines = c.get(f"/runs/{acc['run_id']}/events.jsonl").text.strip().split("\n")
        rows = [json.loads(x) for x in lines]
        assert rows[-1]["msg"]["type"] == "final" and all("t_ms" in r for r in rows)

        ex = c.get(f"/runs/{acc['run_id']}/explain").json()
        assert ex["explanation_markdown"] and ex["steps"]

        assert c.get("/runs/run_nope").status_code == 404


def test_portfolio_endpoints_and_upload():
    with client() as c:
        demo = c.get("/portfolio").json()
        assert demo["portfolio_id"] == "demo" and len(demo["holdings"]) == 8
        csv = "ticker,qty,avg_price,sector\nRELIANCE,10,2800,Energy\nITC.NS,5,,FMCG\n"
        r = c.post("/portfolio/upload?portfolio_id=mine", files={"file": ("p.csv", csv, "text/csv")}).json()
        assert r["portfolio"]["holdings"][0]["ticker"] == "RELIANCE.NS" and r["warnings"]
        assert c.get("/portfolio/mine").json()["holdings"][1]["ticker"] == "ITC.NS"
        saved = c.post("/portfolio", json={"portfolio_id": "x", "holdings": [{"ticker": "TCS.NS", "qty": 1}]}).json()
        assert saved["portfolio_id"] == "x"
        assert c.get("/portfolio/unknown").status_code == 404


def test_scenario_proxy_quota_health_scoreboard(monkeypatch):
    # point quant at a port nothing listens on, so the "down" check does not depend on a real quant running
    monkeypatch.setenv("QUANT_URL", "http://127.0.0.1:59999")
    from copilot_common.settings import reload_settings
    reload_settings()
    with client() as c:
        tr = c.post("/tools/scenario", json={"shocks": {"crude": 10}}).json()
        assert tr["evidence"][0]["tool"] == "scenario"
        q = c.get("/llm/quota").json()
        assert q["mode"] == "local" and "session" in q
        sb = c.get("/backtest/scoreboard").json()          # MOCK=1 + no real run → the 12 §A8 example fixture
        assert sb["_mock"] and set(sb["methods"]) == {"copilot", "price_only", "sentiment_only", "zero"}
        h = c.get("/health").json()
        assert h["service"] == "orchestrator" and h["mock"] is True
        all_ = c.get("/health/all").json()
        assert all_["orchestrator"]["status"] == "ok" and all_["quant"]["status"] == "down"


def test_cluster_status_and_alert_ingest():
    with client() as c:
        st = c.get("/cluster/status").json()
        names = {x["name"] for x in st["services"]}
        assert {"orchestrator", "quant", "sentiment", "agri", "vectordb", "ingestion", "monitor"} <= names
        assert {o["laptop"] for o in st["ollama"]} == {"L1", "L2", "L3"} and "weaviate" in st
        alert = {"alert_id": "al_t", "tier": 3, "kind": "weather_threshold", "tickers": ["ONGC.NS"], "headline": "h",
                 "reason": "r", "impact_score": 0.7, "confidence": 0.8, "created_at": "2026-10-04T00:00:00Z",
                 "cooldown_key": "k", "deeplink": "http://x/run/new?q=a"}
        with c.websocket_connect("/ws/activity") as ws:
            assert c.post("/alerts/ingest", json=alert).json() == {"ok": True}
            assert ws.receive_json() == {"type": "alert", "data": {**alert, "evidence_ids": [], "acknowledged": False,
                                                                    "created_at": "2026-10-04T00:00:00Z"}}
        assert c.get("/alerts/recent").json()[0]["alert_id"] == "al_t"


def test_run_trace_records_services_and_llm_roles():
    """MOCK run: every tool call is traced as fixture (MOCK data), every LLM call as mock, each with its node."""
    with client() as c:
        acc = c.post("/query", json={"query": Q}).json()
        with c.websocket_connect(f"/ws/{acc['run_id']}") as ws:
            while ws.receive_json()["type"] != "final":
                pass
        rows = c.get(f"/runs/{acc['run_id']}/trace").json()
    svc = {r["service"] for r in rows}
    assert {"quant", "vectordb", "ingestion"} <= svc
    assert any(s.startswith("llm:") for s in svc)
    by = {(r["service"], r["node"]) for r in rows}
    assert ("vectordb", "analog_agent") in by and ("quant", "quant_agent") in by
    assert all(r["status"] == "fixture" for r in rows if not r["service"].startswith("llm:"))
    assert all(r["status"] == "mock" for r in rows if r["service"].startswith("llm:"))
    assert all(r["host"].startswith(("L1", "L2", "L3", "mock")) for r in rows), {r["host"] for r in rows}
    assert c.get("/runs/nope/trace").status_code == 404
