import json

import httpx2
import pytest

from copilot_common.settings import reload_settings


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated settings: temp data dir, no real keys, record cache, local mode."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MOCK", "0")
    monkeypatch.setenv("CACHE_MODE", "record")
    monkeypatch.setenv("LLM_MODE", "local")
    monkeypatch.setenv("LLM_REPLAY_STRICT", "0")
    for k in ("GROQ_API_KEY", "CEREBRAS_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(k, "")
    for k in ("OLLAMA_L1", "OLLAMA_L2", "OLLAMA_L3"):
        monkeypatch.setenv(k, f"http://{k.lower().replace('_', '-')}.test:11434")
    reload_settings()
    from copilot_common import reachability
    reachability.reset()
    yield monkeypatch
    reload_settings()


class FakeLLMServer:
    """Routes requests by host + model. rules[(host_substr, model)] = list of responses (popped in order)."""

    def __init__(self):
        self.rules: dict[tuple[str, str], list] = {}
        self.calls: list[dict] = []

    def add(self, host: str, model: str, *responses):
        self.rules.setdefault((host, model), []).extend(responses)

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content or b"{}")
        self.calls.append({"host": request.url.host, "model": body.get("model"), "body": body})
        for (host, model), queue in self.rules.items():
            if host in request.url.host and model == body.get("model") and queue:
                r = queue.pop(0) if len(queue) > 1 else queue[0]
                if isinstance(r, Exception):
                    raise r
                if isinstance(r, tuple):        # (status, headers, json)
                    status, headers, payload = r
                    return httpx2.Response(status, headers=headers, json=payload)
                return httpx2.Response(200, json=completion(r, body.get("model")))
        return httpx2.Response(404, json={"error": "no rule"})


def completion(content: str, model: str, tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"id": "x", "object": "chat.completion", "created": 0, "model": model,
            "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}


@pytest.fixture
def server(env):
    from copilot_llm.gateway import Gateway
    fake = FakeLLMServer()
    gw = Gateway(http_client_factory=lambda: httpx2.AsyncClient(transport=httpx2.MockTransport(fake.handler)))
    return fake, gw
