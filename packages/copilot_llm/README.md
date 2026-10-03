# copilot_llm: LLM gateway (docs/03)

Every LLM call in the system goes through `await llm.chat(role, messages, ...)`.

```python
from copilot_llm import llm
from copilot_common.models import Intent
res = await llm.chat("intent", [{"role": "user", "content": prompt}], schema=Intent, run_id=run_id)
res.ok, res.parsed, res.provider, res.fallbacks, res.cached
```

| Feature | Where |
|---|---|
| 8 providers (Groq ×2, Cerebras, OpenRouter Kimi, 4 Ollama) | `providers.py` |
| Role → chain for `local / boost / auto` | `routing.py` (`ROLE_TABLE`) |
| 429 → cooldown + next provider; 402 → disable; timeouts/unreachable → next | `gateway.py` |
| Rate-limit headers → `data/quota.json` | `quota.py` |
| Response cache `data/cache/llm/<sha>.json` (`CACHE_MODE=record/replay/off`) | `llm_cache.py` |
| JSON repair + one retry, then the next provider | `json_repair.py` |
| Per-run usage, "calls saved" | `usage.py` → `FinalAnswer.llm_usage` |
| Chaos: `FORCE_RATE_LIMIT` contextvar → fake 429 on every cloud call | `chaos.py` |
| `MOCK=1` → `copilot_common/fixtures/llm/<role or fixture>.json` | `gateway._mock` |

## Notes from building it (verified against the installed libraries)
- **Thinking off.** Ollama's OpenAI endpoint takes `reasoning_effort`, not `think`. The gateway sends `reasoning_effort:"none"` and `/no_think` **only to qwen3 models**, because non-thinking models such as gemma3 reject thinking options.
- **Context length.** `num_ctx` cannot be set through the OpenAI endpoint. Start Ollama with `OLLAMA_CONTEXT_LENGTH=8192` (and `OLLAMA_KEEP_ALIVE=30m`) on every laptop.
- **JSON roles.** Local models get `response_format: json_schema` (with an automatic retry in plain `json_object` mode on older Ollama builds). Cloud models get `json_object`, with the schema appended to the system prompt.
- **gpt-oss on Groq/Cerebras** gets `reasoning_effort:"low"` and +1024 `max_tokens` headroom for hidden reasoning tokens. This keeps calls under the free-tier tokens-per-minute limit.
- `chat(..., fixture="narrator_weather_agent")` picks an agent-specific mock fixture (an addition to the 03 spec).
- The openai SDK installed here uses `httpx2`. Tests inject `httpx2.MockTransport` through `Gateway(http_client_factory=...)`.

## Test
```
.venv\Scripts\python -m pytest packages/copilot_llm/tests -q
```
