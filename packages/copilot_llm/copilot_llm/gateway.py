"""Gateway.chat(): routing, fallback, cache, JSON repair, quota and usage (03 §9 step 8)."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import httpx
import openai
from openai import AsyncOpenAI
from pydantic import BaseModel

from copilot_common import reachability
from copilot_common.service_base import FIXTURES_DIR
from copilot_common.settings import get_settings

from . import calls, llm_cache
from .chaos import FORCE_RATE_LIMIT
from .json_repair import parse_as
from .providers import Provider, build_providers
from .quota import QuotaTracker, parse_duration
from .routing import ROLE_TABLE, resolve_chain_with_skips
from .usage import SESSION, usage_for

# Ollama keep_alive for every local call and the warm-up load: -1 = keep the model in memory until Ollama stops
KEEP_ALIVE = -1
# a local model that just crashed or timed out is skipped this long (per model, not per host: on one laptop every
# role shares 127.0.0.1:11434, so marking the host down would also take out the working models)
SICK_TTL_S = 60.0


@dataclass
class LLMResult:
    ok: bool
    text: str = ""
    parsed: BaseModel | dict | None = None
    tool_calls: list[dict] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cached: bool = False
    fallbacks: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def provider_label(self) -> str:
        if self.provider.startswith("ollama_"):
            return "ollama@" + self.provider.split("_")[1]
        return self.provider.split("_")[0] if self.provider else ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if isinstance(self.parsed, BaseModel):
            d["parsed"] = self.parsed.model_dump(mode="json")
        return d


class _CallFailed(Exception):
    def __init__(self, note: str):
        super().__init__(note)
        self.note = note


def _est_tokens(messages: list[dict], max_tokens: int | None) -> int:
    return len(json.dumps(messages, default=str)) // 4 + (max_tokens or 500)


class Gateway:
    def __init__(self, http_client_factory: Callable[[], Any] | None = None) -> None:
        self._quota: QuotaTracker | None = None
        self._clients: dict[str, AsyncOpenAI] = {}
        self._perf: dict[str, list[float]] = {}          # provider -> [tokens_out_total, seconds_total]
        self._sick_until: dict[str, float] = {}           # provider -> skip until (model crashed / timed out)
        self.http_client_factory = http_client_factory    # tests inject a mock transport here
        # warm-up: "pending" until warmup() has loaded every installed local model once (see is_warm)
        self.warm: dict[str, Any] = {"state": "pending", "models": {}, "started_at": None, "finished_at": None}

    def is_warm(self) -> bool:
        """True once warm-up finished (or in MOCK mode). Callers use keyword/template fallbacks until then
        instead of spending their time budget on a model that is still loading."""
        return get_settings().MOCK or self.warm["state"] == "ready"

    # ---------- plumbing ----------
    @property
    def quota(self) -> QuotaTracker:
        if self._quota is None:
            self._quota = QuotaTracker()
        return self._quota

    def reset(self) -> None:
        """For tests: drop clients and quota state so new Settings take effect."""
        self._quota = None
        self._clients.clear()
        self._perf.clear()
        self._sick_until.clear()
        self.warm = {"state": "pending", "models": {}, "started_at": None, "finished_at": None}

    def _client(self, p: Provider) -> AsyncOpenAI:
        key = f"{p.base_url}|{p.api_key[:6]}"
        if key not in self._clients:
            kw = {"http_client": self.http_client_factory()} if self.http_client_factory else {}
            self._clients[key] = AsyncOpenAI(base_url=p.base_url, api_key=p.api_key, max_retries=0, **kw)
        return self._clients[key]

    @staticmethod
    def _prepare(p: Provider, messages: list[dict], schema: type[BaseModel] | None) -> tuple[list[dict], Any, dict]:
        msgs = [dict(m) for m in messages]
        extra: dict = {}
        response_format: Any = None
        if p.kind == "local":
            if p.model.startswith("qwen3"):
                # Thinking off: reasoning_effort for Ollama's OpenAI endpoint, plus /no_think as a belt-and-braces switch.
                extra["reasoning_effort"] = "none"
                sys_idx = next((i for i, m in enumerate(msgs) if m.get("role") == "system"), None)
                if sys_idx is None:
                    msgs.insert(0, {"role": "system", "content": "/no_think"})
                elif "/no_think" not in str(msgs[sys_idx]["content"]):
                    msgs[sys_idx]["content"] = f"{msgs[sys_idx]['content']} /no_think"
            if schema is not None:
                response_format = {"type": "json_schema",
                                   "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()}}
        else:
            if "gpt-oss" in p.model:
                extra["reasoning_effort"] = "low"       # fewer hidden tokens → stays under free-tier TPM
            if schema is not None:
                response_format = {"type": "json_object"}
                instr = ("Return ONLY a JSON object matching this JSON schema: "
                         + json.dumps(schema.model_json_schema(), separators=(",", ":")))
                sys_idx = next((i for i, m in enumerate(msgs) if m.get("role") == "system"), None)
                if sys_idx is None:
                    msgs.insert(0, {"role": "system", "content": instr})
                else:
                    msgs[sys_idx]["content"] = f"{msgs[sys_idx]['content']}\n\n{instr}"
        return msgs, response_format, extra

    async def _call(self, p: Provider, msgs: list[dict], *, temperature: float, tools, response_format, extra: dict,
                    max_tokens: int | None, timeout_s: float):
        if p.kind == "cloud" and FORCE_RATE_LIMIT.get():
            raise _CallFailed(f"{p.name}:429")
        kwargs: dict[str, Any] = {"model": p.model, "messages": msgs, "temperature": temperature,
                                  "timeout": openai.Timeout(timeout_s, connect=reachability.CONNECT_TIMEOUT_S)}
        if tools:
            kwargs["tools"] = tools
        if response_format is not None:
            kwargs["response_format"] = response_format
        if max_tokens:
            kwargs["max_tokens"] = max_tokens + (1024 if "gpt-oss" in p.model else 0)
        if p.kind == "local":
            # keep the model resident: a request without keep_alive would reset it to Ollama's default (5 min)
            extra = {**extra, "keep_alive": KEEP_ALIVE}
        if extra:
            kwargs["extra_body"] = extra
        client = self._client(p)
        try:
            raw = await client.chat.completions.with_raw_response.create(**kwargs)
        except openai.BadRequestError:
            if p.kind != "local" or not (extra or response_format):
                raise _CallFailed(f"{p.name}:400")
            # Older Ollama builds reject reasoning_effort / json_schema: retry once in plain JSON mode.
            kwargs.pop("extra_body", None)
            if response_format is not None:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                raw = await client.chat.completions.with_raw_response.create(**kwargs)
            except openai.APIError as e:  # noqa: F841
                raise _CallFailed(f"{p.name}:400")
        except openai.RateLimitError as e:
            retry = parse_duration(e.response.headers.get("retry-after")) if e.response is not None else None
            self.quota.mark_429(p.name, retry)
            raise _CallFailed(f"{p.name}:429")
        except openai.APIStatusError as e:
            if e.status_code == 402:
                self.quota.disable(p.name, "402 negative balance")
            raise _CallFailed(f"{p.name}:{e.status_code}")
        except openai.APITimeoutError:
            raise _CallFailed(f"{p.name}:timeout")
        except (openai.APIConnectionError, httpx.HTTPError):
            reachability.mark_down(p.base_url)          # skip this host for 30 s instead of paying the connect delay again
            raise _CallFailed(f"{p.name}:unreachable")
        reachability.mark_up(p.base_url)
        if p.kind == "cloud":
            self.quota.update_from_headers(p.name, raw.headers)
        resp = raw.parse()
        choice = resp.choices[0]
        text = choice.message.content or ""
        tool_calls = []
        for tc in choice.message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except ValueError:
                args = {"_raw": tc.function.arguments}
            tool_calls.append({"id": tc.id, "name": tc.function.name, "arguments": args})
        usage = resp.usage
        return text, tool_calls, (usage.prompt_tokens if usage else 0) or 0, (usage.completion_tokens if usage else 0) or 0

    # ---------- mock ----------
    async def _mock(self, role: str, fixture: str | None, schema: type[BaseModel] | None, run_id: str | None,
                    on_event: Callable | None) -> LLMResult:
        await asyncio.sleep(get_settings().MOCK_DELAY_MS / 2000)
        path = FIXTURES_DIR / "llm" / f"{fixture or role}.json"
        if not path.is_file():
            path = FIXTURES_DIR / "llm" / f"{role}.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"text": "", "parsed": None}
        parsed: Any = data.get("parsed")
        text = data.get("text") or (json.dumps(parsed) if parsed is not None else "")
        if schema is not None and parsed is not None:
            parsed = schema.model_validate(parsed)
        res = LLMResult(ok=True, text=text, parsed=parsed, provider="mock", model="mock", latency_ms=1)
        usage_for(run_id).record("mock", "local", "mock", 0, 0, False, 0)
        calls.record(role, "mock", "mock", "mock", 1, "mock", run_id)
        if on_event:
            await _maybe_await(on_event({"provider": "mock", "provider_name": "mock", "model": "mock", "latency_ms": 1,
                                         "tokens_in": 0, "tokens_out": 0, "cached": False, "fallbacks": []}))
        return res

    # ---------- public ----------
    async def chat(self, role: str, messages: list[dict], *, tools: list[dict] | None = None,
                   schema: type[BaseModel] | None = None, mode: str | None = None, timeout_s: float = 60,
                   run_id: str | None = None, on_event: Callable | None = None, fixture: str | None = None,
                   max_tokens: int | None = None, temperature: float | None = None) -> LLMResult:
        s = get_settings()
        if s.MOCK:
            return await self._mock(role, fixture, schema, run_id, on_event)
        mode = mode or s.LLM_MODE
        spec = ROLE_TABLE[role]
        temp = spec.temperature if temperature is None else temperature
        chain, skips = resolve_chain_with_skips(role, mode, self.quota, _est_tokens(messages, max_tokens),
                                                build_providers())
        fallbacks: list[str] = list(skips)
        prepared = [(p, *self._prepare(p, messages, schema)) for p in chain]

        def cache_key(p, msgs, rf):
            return llm_cache.key(p.model, msgs, tools, temp, rf)

        async def finish(p: Provider, text: str, tool_calls: list, t_in: int, t_out: int, ms: int, cached: bool,
                         parsed: Any) -> LLMResult:
            res = LLMResult(ok=True, text=text, parsed=parsed, tool_calls=tool_calls, provider=p.name, model=p.model,
                            latency_ms=ms, tokens_in=t_in, tokens_out=t_out, cached=cached, fallbacks=fallbacks)
            n_fb = sum(1 for f in fallbacks if "skipped" not in f)
            for u in (usage_for(run_id), SESSION):
                u.record(p.name, p.kind, p.model, t_in, t_out, cached, n_fb)
            calls.record(role, p.name, p.model, p.host_label, ms, calls.status_for(role, p.name, cached, mode), run_id,
                         fallbacks)
            if not cached and t_out:
                perf = self._perf.setdefault(p.name, [0.0, 0.0])
                perf[0] += t_out
                perf[1] += ms / 1000
            if on_event:
                await _maybe_await(on_event({"provider": p.host_label, "provider_name": p.name, "model": p.model,
                                             "latency_ms": ms, "tokens_in": t_in, "tokens_out": t_out,
                                             "cached": cached, "fallbacks": list(fallbacks)}))
            return res

        def from_cache(p, msgs, rf):
            rec = llm_cache.get(cache_key(p, msgs, rf))
            if not rec:
                return None
            text = rec["response"].get("text", "")
            parsed = None
            if schema is not None:
                parsed, err = parse_as(text, schema)
                if err:
                    return None
            return rec, text, parsed

        # replay: any cached answer along the chain wins; strict mode never calls a model
        use_cache = not calls.NO_CACHE.get()
        if s.CACHE_MODE == "replay" and use_cache:
            for p, msgs, rf, _extra in prepared:
                hit = from_cache(p, msgs, rf)
                if hit:
                    rec, text, parsed = hit
                    return await finish(p, text, rec["response"].get("tool_calls", []), 0, 0, 0, True, parsed)
            if s.LLM_REPLAY_STRICT:
                return LLMResult(ok=False, fallbacks=fallbacks + ["cache:miss(strict)"], error="replay cache miss")

        for p, msgs, rf, extra in prepared:
            if s.CACHE_MODE == "record" and use_cache:
                hit = from_cache(p, msgs, rf)
                if hit:
                    rec, text, parsed = hit
                    return await finish(p, text, rec["response"].get("tool_calls", []), 0, 0, 0, True, parsed)
            last = p is prepared[-1][0]                  # the chain's final model is never skipped
            if not last and self._sick_until.get(p.name, 0.0) > time.time():
                fallbacks.append(f"{p.name}:skipped(failing)")
                continue
            if reachability.is_down(p.base_url):
                fallbacks.append(f"{p.name}:skipped(down)")
                continue
            t0 = time.perf_counter()
            try:
                text, tool_calls, t_in, t_out = await self._call(
                    p, msgs, temperature=temp, tools=tools, response_format=rf, extra=extra,
                    max_tokens=max_tokens, timeout_s=timeout_s)
                parsed = None
                if schema is not None:
                    parsed, err = parse_as(text, schema)
                    if err:
                        # one repair retry on the same provider (03 §8.4)
                        retry_msgs = msgs + [{"role": "assistant", "content": text},
                                             {"role": "user", "content": f"Your previous output was invalid: {err}. "
                                                                         "Return ONLY valid JSON matching the schema."}]
                        text2, tool_calls, t_in2, t_out2 = await self._call(
                            p, retry_msgs, temperature=temp, tools=tools, response_format=rf, extra=extra,
                            max_tokens=max_tokens, timeout_s=timeout_s)
                        t_in, t_out = t_in + t_in2, t_out + t_out2
                        parsed, err = parse_as(text2, schema)
                        text = text2
                        if err:
                            raise _CallFailed(f"{p.name}:invalid_json")
            except _CallFailed as e:
                fallbacks.append(e.note)
                # a local model that crashes (HTTP 5xx, e.g. a CUDA error in llama-server) or hangs is skipped for
                # SICK_TTL_S, so every later call goes straight to the fallback instead of waiting on it again
                code = e.note.rsplit(":", 1)[-1]
                if p.kind == "local" and not last and (code == "timeout" or code.startswith("5")):
                    self._sick_until[p.name] = time.time() + SICK_TTL_S
                continue
            except Exception as e:  # noqa: BLE001  never crash the caller
                fallbacks.append(f"{p.name}:error({type(e).__name__})")
                continue
            ms = int((time.perf_counter() - t0) * 1000)
            if s.CACHE_MODE != "off":
                llm_cache.put(cache_key(p, msgs, rf), {"provider": p.name, "model": p.model,
                                                       "response": {"text": text, "tool_calls": tool_calls},
                                                       "usage": {"tokens_in": t_in, "tokens_out": t_out}})
            return await finish(p, text, tool_calls, t_in, t_out, ms, False, parsed)

        calls.record(role, "", "", "", 0, "failed", run_id, fallbacks)
        return LLMResult(ok=False, fallbacks=fallbacks, error="all LLM providers failed")

    async def warmup(self, timeout_s: float = 180) -> dict[str, str]:
        """Load every installed local model into memory with keep_alive=-1 (first load takes ~40 s on a laptop
        GPU), via Ollama's native /api/generate with an empty prompt. Sets self.warm; is_warm() turns True when
        every reachable, installed model has answered once. Missing models / down hosts are reported, not waited on."""
        if get_settings().MOCK:
            self.warm = {"state": "ready", "models": {}, "started_at": None, "finished_at": None}
            return {}
        self.warm = {"state": "warming", "models": {}, "started_at": time.time(), "finished_at": None}
        h = await self.health()
        out: dict[str, str] = {}
        for p in build_providers().values():
            if p.kind != "local" or f"{p.model}@{p.base_url}" in out:
                continue
            key = f"{p.model}@{p.base_url}"
            base = p.base_url.removesuffix("/v1")
            entry = h["ollama"].get(base, {})
            if entry.get("status") != "ok":
                out[key] = "host down"
                self.warm["models"][p.model] = out[key]
                continue
            if p.model in entry.get("missing_models", []):
                out[key] = "not pulled"
                self.warm["models"][p.model] = out[key]
                continue
            self.warm["models"][p.model] = "loading"
            t0 = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=reachability.CONNECT_TIMEOUT_S)) as c:
                    r = await c.post(f"{base}/api/generate", json={"model": p.model, "prompt": "", "keep_alive": KEEP_ALIVE})
                    r.raise_for_status()
                out[key] = f"loaded in {time.perf_counter() - t0:.1f}s"
            except Exception as e:  # noqa: BLE001
                out[key] = f"failed: {type(e).__name__}"
            self.warm["models"][p.model] = out[key]
        loaded = any(v.startswith("loaded") for v in out.values())
        self.warm["state"] = "ready" if loaded else "failed"
        self.warm["finished_at"] = time.time()
        return out

    async def health(self) -> dict:
        """Ollama /api/tags per host, quota snapshot, measured tokens/sec per provider."""
        hosts: dict[str, dict] = {}
        for p in build_providers().values():
            if p.kind != "local":
                continue
            base = p.base_url.removesuffix("/v1")
            if base in hosts:
                hosts[base]["roles"].append(p.name)
                continue
            entry = {"roles": [p.name], "status": "down", "models": []}
            try:
                async with httpx.AsyncClient(timeout=2) as c:
                    r = await c.get(f"{base}/api/tags")
                    r.raise_for_status()
                    entry["models"] = [m.get("name") for m in r.json().get("models", [])]
                    entry["status"] = "ok"
            except Exception as e:  # noqa: BLE001
                entry["error"] = type(e).__name__
            hosts[base] = entry
        for base, entry in hosts.items():
            needed = {build_providers()[r].model for r in entry["roles"]}
            entry["missing_models"] = sorted(m for m in needed if entry["status"] == "ok"
                                             and not any(x == m or x.startswith(m + ":") or x == f"{m}:latest"
                                                         for x in entry["models"]))
        tps = {k: round(v[0] / v[1], 1) for k, v in self._perf.items() if v[1] > 0}
        return {"ollama": hosts, "quota": self.quota.snapshot(), "tokens_per_s": tps,
                "session_usage": SESSION.to_dict(self.quota.snapshot()), "mode": get_settings().LLM_MODE,
                "mock": get_settings().MOCK}


async def _maybe_await(x):
    if asyncio.iscoroutine(x):
        await x


llm = Gateway()
