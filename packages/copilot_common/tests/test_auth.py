"""X-Cluster-Key: servers reject requests without it; clients add it only for our own services."""
import asyncio

import httpx
import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from copilot_common import auth
from copilot_common.service_base import create_service_app
from copilot_common.settings import reload_settings


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv("CLUSTER_KEY", "s3cret")
    monkeypatch.setenv("QUANT_URL", "http://10.0.0.2:8101")
    reload_settings()
    yield
    monkeypatch.delenv("CLUSTER_KEY", raising=False)
    reload_settings()


def _app():
    app = create_service_app("t")

    @app.get("/thing")
    async def thing():
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(w: WebSocket):
        await w.accept()
        await w.send_json({"hi": 1})
        await w.close()
    return app


def test_requests_need_the_key(keyed):
    c = TestClient(_app())
    assert c.get("/health").status_code == 200                          # liveness stays open
    assert c.get("/thing").status_code == 401
    assert c.get("/thing", headers={"X-Cluster-Key": "wrong"}).status_code == 401
    assert c.get("/thing", headers={"X-Cluster-Key": "s3cret"}).json() == {"ok": True}
    assert c.get("/thing?key=s3cret").status_code == 200


def test_websocket_needs_the_key(keyed):
    c = TestClient(_app())
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws") as w:
            w.receive_json()
    with c.websocket_connect("/ws?key=s3cret") as w:
        assert w.receive_json() == {"hi": 1}


def test_no_key_configured_means_auth_off(monkeypatch):
    monkeypatch.delenv("CLUSTER_KEY", raising=False)
    reload_settings()
    assert TestClient(_app()).get("/thing").status_code == 200


def test_client_hook_sends_key_only_to_peers(keyed):
    auth.install_httpx_hook()
    seen = {}

    def handler(request: httpx.Request):
        seen[str(request.url.host)] = request.headers.get("X-Cluster-Key")
        return httpx.Response(200, json={})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await c.get("http://10.0.0.2:8101/exposure")                 # our quant service
            await c.get("https://api.open-meteo.com/v1/forecast")      # an outside API
    asyncio.run(go())
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        c.get("http://127.0.0.1:11434/api/tags")                         # Ollama: never gets the key
    assert seen == {"10.0.0.2": "s3cret", "api.open-meteo.com": None, "127.0.0.1": None}
