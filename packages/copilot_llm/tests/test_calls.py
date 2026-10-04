"""LLM call log (Phase 5): own model = ok, another model = fallback; served at GET /llm/calls by every service."""
from copilot_llm import calls


def test_status_for_roles():
    assert calls.status_for("narrator", "ollama_L2", False) == "ok"
    assert calls.status_for("narrator", "ollama_L1", False) == "fallback"
    assert calls.status_for("red_team", "ollama_L3_red", False) == "ok"
    assert calls.status_for("red_team", "ollama_L1", False) == "fallback"
    assert calls.status_for("synthesizer", "ollama_L1", True) == "cached"
    assert calls.status_for("intent", "mock", False) == "mock"
    assert calls.status_for("synthesizer", "groq_oss120b", False, mode="boost") == "ok"


def test_record_and_recent_since():
    calls.clear()
    a = calls.record("alert", "ollama_L3_fast", "qwen3:1.7b", "ollama@L3", 120, "ok")
    calls.record("alert", "ollama_L1", "qwen3:4b-instruct", "ollama@L1", 300, "fallback", fallbacks=["ollama_L3_fast:404"])
    assert [r["status"] for r in calls.recent()] == ["ok", "fallback"]
    assert len(calls.recent(since=a["ts"])) == 2 and calls.recent(since="9999") == []


def test_service_exposes_llm_calls():
    from fastapi.testclient import TestClient
    from copilot_common.service_base import create_service_app
    calls.clear()
    calls.record("sentiment2", "ollama_L2", "gemma3:4b", "ollama@L2", 80, "ok")
    rows = TestClient(create_service_app("x")).get("/llm/calls").json()
    assert rows[0]["model"] == "gemma3:4b" and rows[0]["status"] == "ok"
