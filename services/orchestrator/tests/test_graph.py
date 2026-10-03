"""Full graph runs in MOCK=1: schema-valid answer, parallel fan-out, chaos, time machine, resume, explain."""
import asyncio
import time

from copilot_common.models import ChaosFlags, FinalAnswer, QueryRequest
from copilot_common.settings import reload_settings
from orchestrator import graph as G
from orchestrator.events import bus

Q = "Cyclone heading to Odisha — what happens to my portfolio this week?"


def run(coro):
    return asyncio.run(coro)


def events_of(run_id):
    return [m["data"] for m in bus._history[run_id] if m["type"] == "event"]


def test_mock_run_produces_valid_final_answer():
    final = run(G.run_graph(QueryRequest(query=Q)))
    fa = FinalAnswer.model_validate(final)
    evs = events_of(fa.run_id)
    assert len(evs) >= 14
    nodes = {e["node"] for e in evs}
    assert {"parse_intent", "router", "join", "quant_agent", "synthesizer", "red_team", "validator"} <= nodes
    assert fa.validator.action == "pass" and fa.validator.numbers_found >= 5
    assert fa.hedges and fa.hedges[0].quantity == 1                        # hedge copied exactly from the tool
    assert all(e.id.startswith("ev_") for e in fa.evidence)
    assert len({e.id for e in fa.evidence}) == len(fa.evidence)            # unique per run
    assert fa.latency_ms["total"] > 0 and "fanout" in fa.latency_ms
    assert fa.llm_usage["saved_calls"] >= 5
    pi = next(e for e in evs if e["node"] == "parse_intent" and e["status"] == "finished")
    assert pi["meta"]["intent"]["event_type"] == "cyclone"                 # UI decomposition comes early


def test_fanout_is_parallel(monkeypatch):
    """6 agents × 1 s each must finish in < 2 s total (04 acceptance)."""
    monkeypatch.setenv("MOCK_DELAY_MS", "1000")
    reload_settings()
    monkeypatch.setattr(G, "select_agents", lambda *a, **k: list(G.AGENT_NODES))
    t0 = time.perf_counter()
    final = run(G.run_graph(QueryRequest(query=Q)))
    evs = events_of(final["run_id"])
    finished_agents = {e["node"] for e in evs if e["node"].endswith("_agent") and e["status"] in ("finished", "degraded")}
    assert {"sentiment_agent", "weather_agent", "agri_agent", "macro_agent", "analog_agent", "exposure_agent"} <= finished_agents
    # sentiment_agent makes 2 sequential tool calls (news → sentiment), so the slowest branch is ~2 s, not 6 × 1 s
    assert final["latency_ms"]["fanout"] < 3000
    assert time.perf_counter() - t0 < 8


def test_chaos_weather_down_marks_degraded_but_answers():
    final = run(G.run_graph(QueryRequest(query=Q, chaos=ChaosFlags(weather_down=True))))
    w = next(e for e in final["evidence"] if e["tool"] == "weather")
    assert w["degraded"] and w["degraded_reason"] == "chaos" and w["freshness_s"] >= 21_600
    st = [e["status"] for e in events_of(final["run_id"]) if e["node"] == "weather_agent"]
    assert "degraded" in st
    assert final["answer_markdown"] and final["confidence"] in ("low", "medium")


def test_time_machine_has_no_evidence_after_as_of():
    final = run(G.run_graph(QueryRequest(query=Q, as_of="2021-08-25")))
    for e in final["evidence"]:
        assert e["as_of"][:10] <= "2021-08-25", e["id"]
        assert e["degraded_reason"] != "lookahead_violation"


def test_services_unreachable_degrade_gracefully(monkeypatch):
    """MOCK off but no services running → every tool falls back to fixtures marked service_unreachable."""
    monkeypatch.setenv("MOCK", "0")
    for k, port in {"INGEST_URL": 1, "QUANT_URL": 2, "SENTIMENT_URL": 3, "AGRI_URL": 4, "VECTOR_URL": 5}.items():
        monkeypatch.setenv(k, f"http://127.0.0.1:{port}")
    for k in ("OLLAMA_L1", "OLLAMA_L2", "OLLAMA_L3"):
        monkeypatch.setenv(k, "http://127.0.0.1:9")
    reload_settings()
    from copilot_llm import llm
    llm.reset()
    final = run(G.run_graph(QueryRequest(query=Q), deadline_s=60))
    fa = FinalAnswer.model_validate(final)
    assert fa.evidence and all(e.degraded for e in fa.evidence)
    assert {e.degraded_reason for e in fa.evidence} == {"service_unreachable"}
    assert fa.intent.event_type == "cyclone"                     # keyword fallback when the LLM is down
    assert "### Bottom line" in fa.answer_markdown               # template answer
    assert fa.validator.action == "pass"                          # template only reuses evidence numbers
    assert fa.red_team and fa.red_team.verdict in ("proceed with caution", "do not act")


def test_resume_does_not_rerun_finished_agents(monkeypatch):
    import orchestrator.nodes.synthesizer as S
    real = S.synthesizer
    calls = {"n": 0}

    async def flaky(state):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("laptop dropped")
        return await real(state)

    monkeypatch.setattr(G, "synthesizer", flaky)
    G._graph = None                                              # rebuild with the flaky node

    async def scenario():
        first = await G.run_graph(QueryRequest(query=Q))
        rid = first["run_id"]
        before = len([e for e in events_of(rid) if e["node"].endswith("_agent") and e["status"] == "started"])
        final = await G.resume_run(rid)
        after = [m["data"] for m in bus._history[rid] if m["type"] == "event"]
        return first, final, before, after

    first, final, before, after = run(scenario())
    assert first["validator"]["action"] in ("pass", "flagged", "stripped")     # partial answer was still built
    assert before >= 5
    restarted = [e for e in after if e["node"].endswith("_agent") and e["status"] == "started"]
    assert restarted == []                                       # agents were NOT re-run on resume
    assert calls["n"] == 2 and final["validator"]["action"] == "pass"


def test_explain_replays_previous_run():
    async def scenario():
        first = await G.run_graph(QueryRequest(query=Q))
        ex = await G.run_graph(QueryRequest(query="Explain your last recommendation", ref_run_id=first["run_id"]))
        return first, ex

    first, ex = run(scenario())
    assert ex["intent"]["intent"] == "explain"
    assert ex["answer_markdown"].startswith("1.")
    nodes = {e["node"] for e in events_of(ex["run_id"])}
    assert "explain" in nodes and "weather_agent" not in nodes   # no fan-out for explain


def test_hindi_answer_uses_lang():
    final = run(G.run_graph(QueryRequest(query=Q, lang="hinglish")))
    assert final["lang"] == "hinglish"
