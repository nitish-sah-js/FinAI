# 03 — LLM Gateway (`packages/copilot_llm`)

| | |
|---|---|
| **Owner / Laptop** | L1 "Brain" owner (it's a library, so it runs wherever it's imported: orchestrator on L1, monitor on L3) |
| **Depends on** | [01_CONTRACTS.md](01_CONTRACTS.md) (settings, run ID, AgentEvent), [02_NETWORK_INFRA.md](02_NETWORK_INFRA.md) (Ollama hosts) |
| **Provides** | `await llm.chat(role, messages, ...)`, which handles routing, fallback, cache, JSON repair, quota tracking and usage counters |
| **Build when** | H1–H6. The orchestrator (04) needs it from H6 |

**One rule:** no module calls Ollama, Groq or OpenRouter directly. Everything goes through `copilot_llm`, so that a single switch (`LLM_MODE`) moves the whole system between local, boost and auto modes.

---

## 1. Folder layout

```
packages/copilot_llm/
├── copilot_llm/
│   ├── __init__.py          # exports: llm (singleton Gateway), Role, LLMResult
│   ├── providers.py         # Provider dataclass + PROVIDERS registry
│   ├── routing.py           # ROLE_TABLE, resolve_chain(role, mode, quota)
│   ├── gateway.py           # Gateway.chat(), fallback loop
│   ├── quota.py             # QuotaTracker (parses rate-limit headers)
│   ├── llm_cache.py         # response cache keyed by hash(messages, tools, model, params)
│   ├── json_repair.py       # extract/repair JSON, validate against pydantic model
│   ├── usage.py             # per-run counters (cloud/local/cache hits/saved)
│   └── chaos.py             # force_rate_limit simulation
├── tests/
└── pyproject.toml           # deps: openai>=1.40, httpx, pydantic, tenacity(optional), copilot_common
```

---

## 2. Providers

| name | base_url | model | key env | free-tier limits (verify on day 0) |
|---|---|---|---|---|
| `groq_oss120b` | `https://api.groq.com/openai/v1` | `openai/gpt-oss-120b` | `GROQ_API_KEY` | 30 RPM · 1,000 RPD · 8K TPM · ~200K TPD |
| `groq_qwen32b` | same | `qwen/qwen3-32b` | `GROQ_API_KEY` | 60 RPM · 1,000 RPD · 6K TPM |
| `cerebras_oss120b` | `https://api.cerebras.ai/v1` | `gpt-oss-120b` | `CEREBRAS_API_KEY` | 5 RPM · 1M TPD · **8K context** |
| `openrouter_kimi` | `https://openrouter.ai/api/v1` | `moonshotai/kimi-k2.6:free` | `OPENROUTER_API_KEY` | 20 RPM · 50 RPD (unfunded) |
| `ollama_L1` | `${OLLAMA_L1}/v1` | `qwen3:4b-instruct` | `ollama` (dummy) | unlimited |
| `ollama_L2` | `${OLLAMA_L2}/v1` | `gemma3:4b` | dummy | unlimited |
| `ollama_L3_fast` | `${OLLAMA_L3}/v1` | `qwen3:1.7b` | dummy | unlimited |
| `ollama_L3_red` | `${OLLAMA_L3}/v1` | `phi4-mini` | dummy | unlimited |

Groq limits apply **per organization**. Extra keys in the same org share the same quota. A provider without a key is skipped silently.

## 3. Roles and routing table

| Role | Used by (prompt in 05) | `local` mode chain | `boost` mode chain | `auto` mode | temp | JSON? |
|---|---|---|---|---|---|---|
| `intent` | P1 | ollama_L1 | ollama_L1 | local | 0 | yes (`Intent`) |
| `planner` | P2 (extra tools) | ollama_L1 | groq_qwen32b → ollama_L1 | cloud if quota OK | 0 | tools |
| `narrator` | P4–P7 | ollama_L2 → ollama_L1 | ollama_L2 → ollama_L1 | local | 0.2 | yes (`AgentSignal`) |
| `sentiment2` | P3 | ollama_L2 → ollama_L1 | same | local | 0 | yes |
| `synthesizer` | P8 | ollama_L1 | groq_oss120b → groq_qwen32b → cerebras_oss120b → openrouter_kimi → ollama_L1 | cloud if quota OK, else local | 0.2 | no (markdown + JSON tail) |
| `red_team` | P9 | ollama_L3_red → ollama_L1 | same | local | 0.3 | yes (`RedTeamReport`) |
| `explain` | P10 | ollama_L1 | groq_qwen32b → ollama_L1 | local | 0 | no |
| `alert` | P11 | ollama_L3_fast → ollama_L1 | same | local | 0.2 | no |

**auto mode rule:** use the cloud chain for a role only if `quota.remaining(provider).requests_day > AUTO_MIN_RPD` (default 50) and `tokens_minute > estimated_tokens(messages) * 1.2`. Otherwise go local straight away, with no wasted 429.

Cloud budget per query is **at most 2 calls** (synthesizer, plus the planner only when the router leaves gaps). Everything else stays local. That is roughly 400+ boosted queries a day on Groq alone.

## 4. Ollama-specific settings (passed by the gateway)
- `extra_body={"options": {"num_ctx": 8192, "temperature": t}, "think": False, "keep_alive": "30m"}`
- For Qwen3, thinking is disabled with `think: false`. As a belt-and-braces measure, the gateway appends `/no_think` to the system prompt on qwen3 models.
- JSON roles: `response_format={"type":"json_object"}` for cloud providers. For Ollama, use `format` set to the JSON schema (structured outputs) through `extra_body={"format": Model.model_json_schema()}`.

## 5. Response cache
- Key: `sha256(json.dumps({"model","messages","tools","temperature","response_format"}, sort_keys=True))`
- Stored at `data/cache/llm/<sha>.json` as `{"provider","model","response","usage","saved_at"}`.
- `CACHE_MODE=record` reads the cache if present, otherwise calls the model and writes. `replay` reads the cache or raises `CacheMiss` (then falls back to the next provider only if `LLM_REPLAY_STRICT=0`). `off` always calls.
- Cached responses count as `cache_hits` and **do not** consume quota. This is what makes "run it 20 times an hour" free.

## 6. Quota tracker (headers → state)
Parse these headers after every cloud call. Names are as documented by Groq and OpenRouter; check Cerebras on day 0 and handle missing headers gracefully:

| Header | Meaning |
|---|---|
| `x-ratelimit-limit-requests` / `x-ratelimit-remaining-requests` | requests per day (Groq) |
| `x-ratelimit-limit-tokens` / `x-ratelimit-remaining-tokens` | tokens per minute (Groq) |
| `x-ratelimit-reset-requests` / `x-ratelimit-reset-tokens` | e.g. `2m59.56s`, `7.66s` |
| `retry-after` | seconds, on 429 |
| `x-ratelimit-remaining-requests-day`, `x-ratelimit-remaining-tokens-minute` | Cerebras style |

On a 429, mark the provider `cooldown_until = now + retry_after` and move to the next provider immediately (no sleeping). On a 402 (OpenRouter negative balance), disable that provider for the session. The state is persisted to `data/quota.json` so restarts keep it.

## 7. Usage counters ("LLM calls saved")
Per run: `{"cloud_calls", "local_calls", "cache_hits", "fallbacks", "saved_calls", "tokens_in", "tokens_out", "providers": {"groq_oss120b": 1, "ollama_L2": 4}}`.
`saved_calls` = calls that a cloud-only design would have made but that ran locally or from cache, i.e. `local_calls + cache_hits` for roles whose chain contains a cloud provider in boost mode, plus all narrator, red team and alert calls. The orchestrator copies this into `FinalAnswer.llm_usage`, and the frontend shows it as a meter.

## 8. JSON repair
1. Try `json.loads(text)`.
2. Strip ```` ```json ```` fences and any `<think>...</think>` block, then take the substring from the first `{` to the last `}`.
3. Fix trailing commas and single quotes (small regex pass).
4. Validate against the target Pydantic model. If that fails, **one** retry on the same provider with the message `"Your previous output was invalid: <error>. Return ONLY valid JSON matching the schema."`
5. If it still fails, try the next provider in the chain. If all fail, return `LLMResult(ok=False, parsed=None)`. The caller then produces a degraded output and must not crash.

---

## 9. Build prompt (paste into a coding LLM)

```
Build a Python 3.11 package packages/copilot_llm (an async LLM gateway for OpenAI-compatible endpoints).
Dependencies: openai>=1.40 (AsyncOpenAI), httpx, pydantic>=2, copilot_common (our contract package:
Settings, get_run_id()).

Step 1  providers.py
  @dataclass Provider(name, base_url, model, api_key_env, kind: Literal["cloud","local"], max_ctx:int)
  PROVIDERS: dict[str, Provider] with exactly the 8 providers in the table I paste (section 2).
  Local base_urls come from Settings.OLLAMA_L1/L2/L3 + "/v1". A cloud provider whose key env is empty
  is marked unavailable.

Step 2  routing.py
  Role = Literal["intent","planner","narrator","sentiment2","synthesizer","red_team","explain","alert"]
  ROLE_TABLE: dict[Role, RoleSpec(local_chain:list[str], boost_chain:list[str], auto_cloud:bool,
              temperature:float, json:bool)] exactly as section 3.
  def resolve_chain(role, mode, quota: QuotaTracker, est_tokens:int) -> list[Provider]
     local → local_chain; boost → boost_chain; auto → boost_chain if auto_cloud and quota.ok(...)
     for first cloud provider else local_chain. Skip unavailable and cooling-down providers.
     Always guarantee at least one local provider at the end.

Step 3  quota.py
  class QuotaTracker: update_from_headers(provider, headers), mark_429(provider, retry_after_s),
  disable(provider, reason), ok(provider, est_tokens) -> bool, snapshot() -> dict, persist to
  data/quota.json (load on start). Parse durations like "2m59.56s", "7.66s", "1h2m".

Step 4  llm_cache.py
  key(model, messages, tools, temperature, response_format) -> sha256 hex
  get(key) / put(key, record) under data/cache/llm/. Respect Settings.CACHE_MODE (record|replay|off).

Step 5  json_repair.py
  def extract_json(text:str) -> dict | None   (steps 1-3 in section 8)
  def parse_as(text:str, model: type[BaseModel]) -> tuple[BaseModel|None, str|None]  (obj, error)

Step 6  usage.py
  class RunUsage with counters from section 7; registry usage_for(run_id) -> RunUsage; to_dict().

Step 7  chaos.py
  contextvar FORCE_RATE_LIMIT: bool; when True every cloud provider call raises a fake 429
  (to demo fallback).

Step 8  gateway.py
  @dataclass LLMResult(ok:bool, text:str, parsed:BaseModel|dict|None, tool_calls:list, provider:str,
                       model:str, latency_ms:int, tokens_in:int, tokens_out:int, cached:bool,
                       fallbacks:list[str])
  class Gateway:
    async def chat(self, role:Role, messages:list[dict], *, tools:list[dict]|None=None,
                   schema:type[BaseModel]|None=None, mode:str|None=None, timeout_s:float=60,
                   run_id:str|None=None, on_event:Callable|None=None) -> LLMResult
      - mode defaults to Settings.LLM_MODE
      - chain = resolve_chain(...); for each provider:
          cache lookup → if hit return (cached=True)
          build kwargs: model, messages, temperature, tools, timeout;
            local: extra_body={"options":{"num_ctx":8192,"temperature":t},"think":False,"keep_alive":"30m"},
                   and if schema: extra_body["format"]=schema.model_json_schema();
                   if model startswith "qwen3": append " /no_think" to system message
            cloud: if schema: response_format={"type":"json_object"} and append the schema to the system msg
          use client.chat.completions.with_raw_response.create(...) to read headers → quota.update_from_headers
          on 429/402/timeout/connection error: record fallback, continue
          if schema: parse_as; on failure one repair retry on same provider; then continue chain
          success: cache put, usage update, call on_event({"provider","model","latency_ms","tokens"})
      - if all fail: return LLMResult(ok=False, ...)
    async def health(self) -> dict  (GET /api/tags on each Ollama host, quota snapshot)
  llm = Gateway()   # module singleton
  One AsyncOpenAI client per provider (reuse connections).

Step 9  tests/ (pytest + respx or monkeypatch)
  - fallback: first provider 429 → second used, fallbacks recorded
  - replay mode returns cached response without network
  - auto mode goes local when quota.remaining requests < AUTO_MIN_RPD
  - JSON repair handles fences, <think> blocks, trailing commas
  - FORCE_RATE_LIMIT makes boost mode land on ollama
Output complete files, no placeholders.
[PASTE SECTIONS 2-8 OF 03_LLM_GATEWAY.md]
```

---

## 10. Examples

Call:
```python
from copilot_llm import llm
from copilot_common.models import Intent
res = await llm.chat("intent", [{"role":"system","content":P1},{"role":"user","content":"How will Cyclone Dana hit my portfolio this week?"}],
                     schema=Intent, run_id=run_id)
```
`LLMResult` (as dict):
```json
{"ok": true, "provider": "ollama_L1", "model": "qwen3:4b-instruct", "cached": false, "latency_ms": 640,
 "tokens_in": 412, "tokens_out": 71, "fallbacks": [],
 "parsed": {"intent":"event_impact","event_type":"cyclone","region":"Odisha","tickers":[],"asset_classes":["equity"],
            "horizon_days":5,"references_portfolio":true,"needs_tools":["weather","analogs","exposure","risk","sentiment"]}}
```
Synthesizer in auto mode with a forced rate limit:
```json
{"ok": true, "provider": "ollama_L1", "model": "qwen3:4b-instruct", "fallbacks": ["groq_oss120b:429","groq_qwen32b:429","cerebras_oss120b:429","openrouter_kimi:skipped(no key)"], "latency_ms": 5400}
```
Usage snapshot that goes into `FinalAnswer.llm_usage`:
```json
{"cloud_calls": 1, "local_calls": 8, "cache_hits": 2, "fallbacks": 0, "saved_calls": 10,
 "tokens_in": 9120, "tokens_out": 1430, "providers": {"groq_oss120b": 1, "ollama_L1": 2, "ollama_L2": 5, "ollama_L3_red": 1},
 "quota": {"groq_oss120b": {"requests_day_remaining": 962, "tokens_minute_remaining": 7400}}}
```

## 11. Mock mode
With `MOCK=1`, `Gateway.chat` returns canned responses from `copilot_common/fixtures/llm/<role>.json`. The `parsed` field is pre-filled so the orchestrator and UI work with no model running. `provider="mock"`.

## 12. Acceptance checklist
- [ ] `LLM_MODE=local` makes zero network calls outside the LAN (verify with Groq keys unset)
- [ ] `LLM_MODE=boost` with a Groq key uses Groq for the synthesizer, and `quota.json` updates
- [ ] With `force_rate_limit`, the whole chain falls through to local in under 1 s of overhead
- [ ] Re-running the same query in `record` mode the second time gives `cache_hits` > 0 and 0 cloud calls
- [ ] JSON roles return valid Pydantic objects in 95% or more of the runs on the 10 golden queries (05)
- [ ] Measured tokens per second for each local model are logged in `GET /llm/health`

## 13. Integration hooks
- The orchestrator (04) passes `on_event` so every LLM call appears as `AgentEvent.model`, `.provider` and `.tokens_*`.
- The orchestrator exposes `GET /llm/quota` (quota snapshot), which the terminal (13) shows as a quota meter with a "calls saved" counter.
- The monitor (11) imports `llm` for P11 alert text.
- The chaos toggle in the UI sets `ChaosFlags.force_rate_limit`, and the orchestrator sets the `chaos.FORCE_RATE_LIMIT` contextvar for that run.

## 14. Sources
- Groq rate limits and headers: https://console.groq.com/docs/rate-limits · https://benchlm.ai/free-tier/groq
- Groq OpenAI compatibility: https://console.groq.com/docs/openai
- Cerebras free tier: https://www.free-model.com/providers/cerebras/ · https://inference-docs.cerebras.ai/
- OpenRouter limits: https://openrouter.ai/docs/api-reference/limits
- Ollama OpenAI compatibility, thinking and structured outputs: https://docs.ollama.com/api/openai-compatibility · https://docs.ollama.com/capabilities/thinking · https://docs.ollama.com/capabilities/structured-outputs
