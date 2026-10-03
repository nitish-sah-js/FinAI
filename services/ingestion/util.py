"""Shared helpers: config, cache wrapper, Evidence builders, mock/chaos/guard."""
from __future__ import annotations
import asyncio, json, logging, os, time
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, Request
from copilot_common.cache import cached, CacheMiss          # noqa: F401  (CacheMiss re-exported)
from copilot_common.ids import EvidenceCounter, new_run_id
from copilot_common.models import Evidence, ToolResult
from copilot_common.settings import get_settings

from . import store
from .timeutil import utcnow, parse_dt

log = logging.getLogger("ingestion")
HERE = Path(__file__).parent
CFG = HERE / "config"


def load_json(name: str):
    return json.loads((CFG / name).read_text())


def is_mock() -> bool:
    # same source as copilot_common (.env + env vars), so MOCK/CACHE_MODE in .env are honoured
    return bool(get_settings().MOCK)


def is_replay() -> bool:
    return get_settings().CACHE_MODE == "replay"


async def cg(source: str, params: dict, fn, ttl: int | None = None):
    """Raw-source cache: data/cache/ingestion/<source>/<sha256(params)>.json (via copilot_common.cache.cached)."""
    return await cached(f"ingestion/{source}", params, fn, ttl_s=ttl)


def mk_ev(counter, rid, tool, value, source, as_of, *, summary=None, confidence=None, degraded=False,
          reason=None, source_url=None, t0=None) -> Evidence:
    ts = utcnow()
    as_of = as_of or ts
    return Evidence(id=counter.next(tool), run_id=rid, tool=tool, value=value, summary=summary, source=source,
                    source_url=source_url, as_of=as_of, timestamp=ts,
                    freshness_s=max(0, int((ts - as_of).total_seconds())), confidence=confidence,
                    degraded=degraded, degraded_reason=reason,
                    latency_ms=int((time.time() - t0) * 1000) if t0 else None)


def degraded_ev(counter, rid, tool, key, source, reason, summary, fallback, base_conf=0.4, t0=None,
                as_of: datetime | None = None) -> Evidence:
    """Last cached good value (if any), confidence x0.5, degraded=True. HTTP stays 200.
    Last-good values are live data, so a time-machine (as_of) request never gets one dated after as_of."""
    last = store.load_last_good(tool, key)
    if last and as_of is not None and parse_dt(last["as_of"]) > as_of:
        last = None
    if last:
        value, as_of = last["value"], parse_dt(last["as_of"])
        conf = round((last.get("confidence") or base_conf) * 0.5, 2)
        src = f"cache ({source})"
    else:
        value, as_of, conf, src = fallback, min(utcnow(), as_of or utcnow()), round(base_conf * 0.5, 2), source
    return mk_ev(counter, rid, tool, value, src, as_of, summary=summary, confidence=conf,
                 degraded=True, reason=reason, t0=t0)


def chaos_set(body_chaos, request: Request) -> tuple[set[str], int]:
    names = {n.strip() for n in request.headers.get("X-Chaos", "").split(",") if n.strip()}
    slow = 0
    if body_chaos is not None:
        d = body_chaos.model_dump() if hasattr(body_chaos, "model_dump") else dict(body_chaos)
        names |= {k for k, v in d.items() if v is True}
        slow = int(d.get("slow_network_ms") or 0)
    return names, slow


async def mock_result(fixture: str) -> ToolResult:
    await asyncio.sleep(get_settings().MOCK_DELAY_MS / 1000)
    res = ToolResult.model_validate(json.loads((HERE / "fixtures" / f"{fixture}.json").read_text()))
    for ev in res.evidence:
        ev.degraded, ev.degraded_reason = True, "mock"
    return res


async def guarded(request: Request, body_chaos, tool: str, fixture: str, key: str, source: str, fn,
                  as_of: datetime | None = None) -> ToolResult:
    """Wraps every endpoint: MOCK, chaos slow-network, and 'never raise' (4xx only for invalid input)."""
    if is_mock():
        return await mock_result(fixture)
    rid = request.headers.get("X-Run-Id") or new_run_id()
    counter, t0 = EvidenceCounter(rid), time.time()
    chaos, slow = chaos_set(body_chaos, request)
    try:
        if slow:
            await asyncio.sleep(slow / 1000)
        return await fn(rid, counter, t0, chaos)
    except HTTPException:
        raise
    except Exception as e:
        reason = "cache_miss" if isinstance(e, CacheMiss) else "api_timeout"
        log.exception("%s failed", tool)
        return ToolResult(evidence=[degraded_ev(counter, rid, tool, key, source, reason,
                                                f"{tool} unavailable ({reason})", {"error": str(e)}, t0=t0,
                                                as_of=as_of)],
                          warnings=[f"{tool}: {type(e).__name__}: {e}"])
