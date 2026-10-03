"""REST + WS (11 §8, §12): test alert reaches a WS client and the DB; replay on connect; ack; filters; health."""
from fastapi.testclient import TestClient

from copilot_common.models import Alert, Health


def client():
    import monitor.main as m
    return TestClient(m.app)


def test_test_alert_reaches_ws_client_and_store():
    with client() as c:
        with c.websocket_connect("/ws/alerts") as ws:
            r = c.post("/alerts/test", json={"kind": "weather_threshold", "tickers": ["ONGC.NS"], "tier": 3})
            assert r.status_code == 200
            a = Alert.model_validate(r.json())
            msg = ws.receive_json()
            assert msg["type"] == "alert" and msg["data"]["alert_id"] == a.alert_id and msg["data"]["reason"] == "TEST"
            assert msg["data"]["created_at"].endswith("Z") or "+00:00" in msg["data"]["created_at"]
        listed = c.get("/alerts").json()
        assert [x["alert_id"] for x in listed] == [a.alert_id]


def test_test_alert_defaults_and_validation():
    with client() as c:
        assert c.post("/alerts/test").status_code == 200
        assert c.post("/alerts/test", json={"kind": "bogus"}).status_code == 422
        assert c.post("/alerts/test", json={"tier": 7}).status_code == 422


def test_replay_last_alerts_on_connect_and_ack_broadcast():
    with client() as c:
        ids = [c.post("/alerts/test", json={"tier": t}).json()["alert_id"] for t in (1, 2, 3)]
        with c.websocket_connect("/ws/alerts") as ws:
            replay = [ws.receive_json() for _ in ids]
            assert [m["data"]["alert_id"] for m in replay] == ids and all(m["replay"] for m in replay)
            r = c.post(f"/alerts/{ids[0]}/ack")
            assert r.status_code == 200 and r.json()["acknowledged"] is True
            assert ws.receive_json() == {"type": "ack", "alert_id": ids[0]}
        assert c.post("/alerts/al_nope/ack").status_code == 404
        assert len(c.get("/alerts", params={"tier_min": 2}).json()) == 2
        assert len(c.get("/alerts", params={"limit": 1}).json()) == 1


def test_one_dead_ws_client_does_not_break_delivery():
    from monitor.delivery.ws_hub import hub

    class Dead:
        async def send_text(self, _):
            raise RuntimeError("gone")
    with client() as c:
        hub.clients.add(Dead())
        with c.websocket_connect("/ws/alerts") as ws:
            c.post("/alerts/test")
            assert ws.receive_json()["type"] == "alert"
        assert not any(isinstance(x, Dead) for x in hub.clients)


def test_health_and_state_with_upstreams_down(monkeypatch):
    monkeypatch.setenv("MOCK", "0")
    from copilot_common.settings import reload_settings
    reload_settings()
    import monitor.main as m
    monkeypatch.setattr(m.engine, "start", _noop)       # don't start pollers; poll once by hand
    with client() as c:
        import asyncio
        from monitor import sources
        assert asyncio.run(sources.feed_since(None)) is None          # ingestion down → None, no raise
        h = Health.model_validate(c.get("/health").json())
        assert h.service == "monitor" and h.status == "degraded" and h.deps["ingestion"] == "down"
        st = c.get("/monitor/state").json()
        assert st["deps"]["ingestion"] == "down" and len(st["detectors"]) == 6 and "active_cooldowns" in st


async def _noop(*a, **k):
    from monitor import store
    await store.init_db()
