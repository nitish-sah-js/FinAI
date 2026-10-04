"""Recent LLM calls of this process (Phase 5 usage trace) + a per-request "no cache" switch.

Every Gateway.chat() appends one row: which role asked, which model on which laptop answered, how long it took and
whether it was the role's own model ("ok"), a fallback to another model ("fallback"), a cache hit ("cached"), MOCK
("mock") or nothing answered ("failed"). service_base serves the rows at GET /llm/calls, so scripts/verify_usage.py
can see calls made inside the sentiment and monitor services, not only the orchestrator's.
"""
from __future__ import annotations

import collections
import contextvars
import threading
from datetime import datetime, timezone

from .routing import ROLE_TABLE

# set to True for one request to skip cache reads (verify_usage wants real model calls, not recorded answers)
NO_CACHE: contextvars.ContextVar[bool] = contextvars.ContextVar("NO_CACHE", default=False)

_lock = threading.Lock()
_calls: "collections.deque[dict]" = collections.deque(maxlen=1000)


def status_for(role: str, provider_name: str, cached: bool, mode: str = "local") -> str:
    """'ok' when the role's own (first local) model answered, 'fallback' when another one did."""
    if cached:
        return "cached"
    if provider_name == "mock":
        return "mock"
    spec = ROLE_TABLE.get(role)
    own = spec.local_chain[0] if spec else None
    if mode != "local" and spec and provider_name in spec.boost_chain:
        return "ok"
    return "ok" if provider_name == own else "fallback"


def record(role: str, provider_name: str, model: str, host: str, latency_ms: int, status: str,
           run_id: str | None = None, fallbacks: list[str] | None = None) -> dict:
    row = {"ts": datetime.now(timezone.utc).isoformat(), "role": role, "provider": provider_name, "model": model,
           "host": host, "latency_ms": latency_ms, "status": status, "run_id": run_id,
           "fallbacks": list(fallbacks or [])}
    with _lock:
        _calls.append(row)
    return row


def recent(since: str | None = None, limit: int = 500) -> list[dict]:
    with _lock:
        rows = list(_calls)
    if since:
        rows = [r for r in rows if r["ts"] >= since]
    return rows[-limit:]


def clear() -> None:
    with _lock:
        _calls.clear()
