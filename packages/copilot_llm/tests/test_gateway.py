import asyncio

import pytest

from copilot_common.models import Intent
from copilot_common.settings import reload_settings
from copilot_llm.chaos import FORCE_RATE_LIMIT
from copilot_llm.quota import ProviderQuota
from copilot_llm.usage import usage_for

INTENT_JSON = ('{"intent":"event_impact","event_type":"cyclone","region":"Odisha","tickers":[],'
               '"asset_classes":["equity"],"horizon_days":5,"references_portfolio":true,"needs_tools":["weather"]}')
MSGS = [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}]
GROQ_HEADERS = {"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "962",
                "x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "7400",
                "x-ratelimit-reset-tokens": "7.66s"}


def run(coro):
    return asyncio.run(coro)


def test_local_intent_parses_schema_and_disables_thinking(server):
    fake, gw = server
    fake.add("ollama-l1", "qwen3:4b-instruct", INTENT_JSON)
    res = run(gw.chat("intent", MSGS, schema=Intent, run_id="r1"))
    assert res.ok and res.provider == "ollama_L1" and isinstance(res.parsed, Intent)
    assert res.parsed.region == "Odisha"
    body = fake.calls[0]["body"]
    assert body["reasoning_effort"] == "none"
    assert body["messages"][0]["content"].endswith("/no_think")
    assert body["response_format"]["type"] == "json_schema"


def test_gemma_gets_no_thinking_flag(server):
    fake, gw = server
    fake.add("ollama-l2", "gemma3:4b", '{"agent":"weather_agent","signal":"neutral","summary":"s","evidence_ids":[]}')
    from copilot_common.models import AgentSignal
    res = run(gw.chat("narrator", MSGS, schema=AgentSignal))
    assert res.ok and res.provider == "ollama_L2"
    assert "reasoning_effort" not in fake.calls[0]["body"]


def test_boost_fallback_on_429_records_cooldown(server):
    fake, gw = server
    server[0]  # noqa: B018
    import os
    os.environ["GROQ_API_KEY"] = "gsk_test"
    reload_settings()
    fake.add("api.groq.com", "openai/gpt-oss-120b", (429, {"retry-after": "12"}, {"error": {"message": "rate limited"}}))
    fake.add("api.groq.com", "qwen/qwen3-32b", (200, GROQ_HEADERS,
                                                {"id": "x", "object": "chat.completion", "created": 0, "model": "qwen/qwen3-32b",
                                                 "choices": [{"index": 0, "message": {"role": "assistant", "content": "### Bottom line\nok"},
                                                              "finish_reason": "stop"}],
                                                 "usage": {"prompt_tokens": 50, "completion_tokens": 10}}))
    res = run(gw.chat("synthesizer", MSGS, mode="boost", run_id="r2"))
    assert res.ok and res.provider == "groq_qwen32b"
    assert "groq_oss120b:429" in res.fallbacks
    assert "cerebras_oss120b:skipped(no key)" in res.fallbacks
    assert not gw.quota.available("groq_oss120b")                     # 12 s cooldown
    snap = gw.quota.snapshot()["groq_qwen32b"]
    assert snap["requests_day_remaining"] == 962 and snap["tokens_minute_remaining"] == 7400
    u = usage_for("r2").to_dict()
    assert u["cloud_calls"] == 1 and u["fallbacks"] == 1
    os.environ["GROQ_API_KEY"] = ""


def test_force_rate_limit_lands_on_local(server):
    fake, gw = server
    import os
    os.environ["GROQ_API_KEY"] = "gsk_test"
    reload_settings()
    fake.add("ollama-l1", "qwen3:4b-instruct", "local answer")
    token = FORCE_RATE_LIMIT.set(True)
    try:
        res = run(gw.chat("synthesizer", MSGS, mode="boost"))
    finally:
        FORCE_RATE_LIMIT.reset(token)
        os.environ["GROQ_API_KEY"] = ""
    assert res.ok and res.provider == "ollama_L1" and res.text == "local answer"
    assert {"groq_oss120b:429", "groq_qwen32b:429", "cerebras_oss120b:skipped(no key)"} <= set(res.fallbacks)
    assert all(c["host"] != "api.groq.com" for c in fake.calls)       # no real cloud call was made


def test_auto_goes_local_when_daily_quota_low(server):
    fake, gw = server
    import os
    os.environ["GROQ_API_KEY"] = "gsk_test"
    reload_settings()
    gw.quota.state["groq_oss120b"] = ProviderQuota(requests_day_remaining=10, requests_day_limit=1000)
    fake.add("ollama-l1", "qwen3:4b-instruct", "local")
    res = run(gw.chat("synthesizer", MSGS, mode="auto"))
    os.environ["GROQ_API_KEY"] = ""
    assert res.provider == "ollama_L1" and res.fallbacks == []


def test_record_then_replay_uses_cache_without_network(server):
    fake, gw = server
    fake.add("ollama-l1", "qwen3:4b-instruct", INTENT_JSON)
    first = run(gw.chat("intent", MSGS, schema=Intent, run_id="r3"))
    assert first.ok and not first.cached
    again = run(gw.chat("intent", MSGS, schema=Intent, run_id="r3"))
    assert again.cached and len(fake.calls) == 1                   # record mode reuses the cache
    import os
    os.environ["CACHE_MODE"] = "replay"
    os.environ["LLM_REPLAY_STRICT"] = "1"
    reload_settings()
    fake.rules.clear()
    third = run(gw.chat("intent", MSGS, schema=Intent))
    assert third.ok and third.cached and len(fake.calls) == 1
    miss = run(gw.chat("intent", [{"role": "user", "content": "new"}], schema=Intent))
    assert not miss.ok and "cache:miss(strict)" in miss.fallbacks
    assert usage_for("r3").to_dict()["cache_hits"] == 1


def test_invalid_json_repair_retry_then_success(server):
    fake, gw = server
    fake.add("ollama-l1", "qwen3:4b-instruct", "sorry, here: {intent: bad}", INTENT_JSON)
    res = run(gw.chat("intent", MSGS, schema=Intent))
    assert res.ok and res.parsed.event_type == "cyclone"
    assert len(fake.calls) == 2
    assert "previous output was invalid" in fake.calls[1]["body"]["messages"][-1]["content"]


def test_all_fail_returns_not_ok(server):
    fake, gw = server                     # no rules → 404 from every host
    res = run(gw.chat("narrator", MSGS))
    assert not res.ok
    assert res.fallbacks == ["ollama_L2:404", "ollama_L1:404"]


def test_mock_mode_returns_fixture(env):
    env.setenv("MOCK", "1")
    env.setenv("MOCK_DELAY_MS", "0")
    reload_settings()
    from copilot_llm.gateway import Gateway
    res = run(Gateway().chat("intent", MSGS, schema=Intent))
    assert res.ok and res.provider == "mock" and res.parsed.intent == "event_impact"
    syn = run(Gateway().chat("synthesizer", MSGS))
    assert "<json>" in syn.text


@pytest.mark.parametrize("text", [
    "```json\n{\"a\": 1,}\n```",
    "<think>hmm {not json}</think> {\"a\": 1}",
    "Here you go: {'a': 1}",
])
def test_json_repair(text):
    from copilot_llm.json_repair import extract_json
    assert extract_json(text) == {"a": 1}


def test_parse_duration():
    from copilot_llm.quota import parse_duration
    assert parse_duration("2m59.56s") == pytest.approx(179.56)
    assert parse_duration("7.66s") == pytest.approx(7.66)
    assert parse_duration("1h2m") == 3720
    assert parse_duration("12") == 12
    assert parse_duration("250ms") == pytest.approx(0.25)


def test_crashing_local_model_is_skipped_after_first_failure(server, monkeypatch):
    """Seen live on L2: gemma3 crashed in llama-server (HTTP 500, CUDA 0xc0000409) on every call, and each agent
    waited on it until its time budget ran out. After one 5xx the model is skipped for SICK_TTL_S."""
    import copilot_llm.gateway as G
    fake, gw = server
    crash = (500, {}, {"error": "llama-server process has terminated: exit status 0xc0000409"})
    fake.add("ollama-l2", "gemma3:4b", crash)
    fake.add("ollama-l1", "qwen3:4b-instruct", "fallback answer")
    first = run(gw.chat("narrator", MSGS))
    assert first.ok and first.provider == "ollama_L1" and first.fallbacks == ["ollama_L2:500"]
    second = run(gw.chat("narrator", MSGS, run_id="r2"))
    assert second.ok and second.fallbacks == ["ollama_L2:skipped(failing)"]
    assert sum(1 for c in fake.calls if c["model"] == "gemma3:4b") == 1          # not asked again
    monkeypatch.setattr(G.time, "time", lambda: 10 ** 12)                        # after the TTL: tried again
    run(gw.chat("narrator", MSGS, run_id="r3"))
    assert sum(1 for c in fake.calls if c["model"] == "gemma3:4b") == 2


def test_last_model_in_chain_is_never_skipped(server):
    fake, gw = server
    crash = (500, {}, {"error": "boom"})
    fake.add("ollama-l1", "qwen3:4b-instruct", crash, "ok now")
    assert not run(gw.chat("intent", MSGS)).ok
    assert run(gw.chat("intent", MSGS)).text == "ok now"                        # L1 is the end of the chain
