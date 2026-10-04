"""Cold start: until the model is warm, parse_intent uses keyword rules instead of waiting on a loading model."""
import asyncio

from copilot_common.models import QueryRequest
from copilot_common.settings import reload_settings


def test_keyword_intent_while_model_warming(monkeypatch):
    monkeypatch.setenv("MOCK", "0")                      # is_warm() is always True in MOCK mode
    reload_settings()
    from copilot_llm import llm
    from orchestrator.nodes.parse_intent import parse_intent

    llm.warm = {"state": "warming", "models": {}, "started_at": None, "finished_at": None}
    called = []

    async def no_chat(*a, **k):                          # the LLM must not be called while warming
        called.append(1)
        raise AssertionError("chat called while warming")

    monkeypatch.setattr("orchestrator.nodes.parse_intent.chat", no_chat)
    state = {"run_id": "run_t", "request": QueryRequest(query="Cyclone near Odisha, hedge my portfolio").model_dump()}
    out = asyncio.run(parse_intent(state))
    assert out["intent"]["event_type"] == "cyclone" and not called


def test_warmup_endpoint_reports_ready_in_mock():
    from fastapi.testclient import TestClient
    from orchestrator.app import app
    with TestClient(app) as c:
        assert c.get("/llm/warmup").json()["state"] == "ready"
