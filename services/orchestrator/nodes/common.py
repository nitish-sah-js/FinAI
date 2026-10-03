"""Helpers shared by all nodes: LLM calls with event plumbing, compact evidence, mode/chaos resolution."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from copilot_common.settings import get_settings
from copilot_llm import FORCE_RATE_LIMIT, LLMResult, llm

from ..budget import Budget
from ..events import bus
from ..staleness import effective_confidence


def run_mode(state: dict) -> str:
    return (state.get("request") or {}).get("llm_mode") or get_settings().LLM_MODE


def chaos(state: dict) -> dict:
    return (state.get("request") or {}).get("chaos") or {}


def as_of(state: dict):
    return (state.get("request") or {}).get("as_of")


async def chat(state: dict, node: str, b: Budget, role: str, messages: list[dict], *,
               schema: type[BaseModel] | None = None, fixture: str | None = None, tools: list[dict] | None = None,
               max_tokens: int | None = None, timeout_s: float = 60) -> LLMResult:
    """llm.chat with this run's mode + chaos, emitting a 'progress' AgentEvent per LLM call."""
    run_id = state["run_id"]

    async def on_event(info: dict) -> None:
        b.add_llm(info)
        note = "cache hit" if info.get("cached") else f"{info['latency_ms']} ms"
        if info.get("fallbacks"):
            note += " · fallbacks: " + ", ".join(info["fallbacks"])
        await bus.emit(run_id, node, "progress", model=info.get("model"), provider=info.get("provider"),
                       tokens_in=info.get("tokens_in"), tokens_out=info.get("tokens_out"),
                       latency_ms=info.get("latency_ms"), message=f"LLM {info.get('model')} · {note}")

    token = FORCE_RATE_LIMIT.set(bool(chaos(state).get("force_rate_limit")))
    try:
        return await llm.chat(role, messages, schema=schema, mode=run_mode(state), run_id=run_id, on_event=on_event,
                              fixture=fixture, tools=tools, max_tokens=max_tokens or b.max_tokens, timeout_s=timeout_s)
    finally:
        FORCE_RATE_LIMIT.reset(token)


def compact(ev: dict, with_value: bool = True) -> dict:
    """Token-saving evidence view for prompts (05 §1)."""
    out = {"id": ev["id"], "tool": ev["tool"], "confidence": _round(effective_confidence(ev)),
           "degraded": ev.get("degraded", False), "as_of": str(ev.get("as_of"))[:16]}
    if with_value:
        out["value"] = ev.get("value")
    else:
        out["summary"] = ev.get("summary")
    return out


def _round(x: Any):
    return round(x, 2) if isinstance(x, (int, float)) else x


def finish_kwargs(b: Budget, evidence_ids: list[str] | None = None, message: str | None = None, **meta) -> dict:
    kw: dict[str, Any] = {"latency_ms": b.ms, "evidence_ids": evidence_ids or [], "message": message,
                          "model": b.model, "provider": b.provider}
    if b.tokens_in or b.tokens_out:
        kw["tokens_in"], kw["tokens_out"] = b.tokens_in, b.tokens_out
    if meta:
        kw["meta"] = meta
    return kw
