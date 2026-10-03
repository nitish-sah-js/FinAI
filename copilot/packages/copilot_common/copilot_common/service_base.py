from __future__ import annotations
import asyncio
import contextvars
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from .ids import EvidenceCounter
from .models import Evidence, Health, ToolResult
from .settings import settings

_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)
_SERVICE: dict[str, str] = {"name": "service"}
FIXTURES = Path(__file__).parent / "fixtures"


def get_run_id() -> str | None:
    return _run_id.get()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def degraded(run_id: str | None, tool: str, reason: str, value: dict | None = None, source: str = "n/a",
             as_of: datetime | None = None, confidence: float | None = 0.25, summary: str | None = None) -> Evidence:
    ts = _now()
    as_of = as_of or ts
    return Evidence(id=EvidenceCounter(run_id).next(tool), run_id=run_id, tool=tool, value=value or {},
                    summary=summary or f"{tool} unavailable ({reason})", source=source, as_of=as_of, timestamp=ts,
                    freshness_s=max(0, int((ts - as_of).total_seconds())), confidence=confidence,
                    degraded=True, degraded_reason=reason)


def create_service_app(name: str, version: str = "0.1.0", deps_check: Callable[[], dict[str, str]] | None = None) -> FastAPI:
    """/health, CORS *, X-Latency-Ms, X-Run-Id propagation, exception -> ToolResult(degraded) (HTTP 200)."""
    _SERVICE["name"] = name
    app = FastAPI(title=f"copilot {name}", version=version)
    started = time.time()
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def _timing(request: Request, call_next):
        t0 = time.perf_counter()
        token = _run_id.set(request.headers.get("X-Run-Id"))
        try:
            resp = await call_next(request)
        finally:
            _run_id.reset(token)
        resp.headers["X-Latency-Ms"] = str(int((time.perf_counter() - t0) * 1000))
        rid = request.headers.get("X-Run-Id")
        if rid:
            resp.headers["X-Run-Id"] = rid
        return resp

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        ev = degraded(request.headers.get("X-Run-Id"), request.url.path.strip("/") or name, "internal_error",
                      source=name, summary=f"{name} internal error: {type(exc).__name__}")
        return JSONResponse(ToolResult(evidence=[ev], warnings=[f"{type(exc).__name__}: {exc}"]).model_dump(mode="json"),
                            status_code=200)

    @app.get("/health", response_model=Health)
    async def _health() -> Health:
        deps: dict[str, str] = {}
        if deps_check:
            try:
                deps = deps_check() or {}
            except Exception:
                deps = {"deps_check": "down"}
        status = "ok" if all(v == "ok" for v in deps.values()) else "degraded"
        return Health(service=name, status=status, version=version, mock=bool(settings.MOCK),
                      uptime_s=int(time.time() - started), deps=deps)

    return app


def mock_or(endpoint_name: str, real_fn: Callable[..., Awaitable[ToolResult]], service: str | None = None):
    """Wrap an endpoint implementation. With MOCK=1, wait MOCK_DELAY_MS and return
    fixtures/<service>/<endpoint>.json with degraded=true, degraded_reason="mock"."""
    async def _wrapped(*args: Any, **kwargs: Any) -> ToolResult:
        if settings.MOCK:
            await asyncio.sleep(settings.MOCK_DELAY_MS / 1000)
            path = FIXTURES / (service or _SERVICE["name"]) / f"{endpoint_name}.json"
            res = ToolResult.model_validate(json.loads(path.read_text()))
            rid = get_run_id()
            for ev in res.evidence:
                ev.degraded, ev.degraded_reason = True, "mock"
                if rid:
                    ev.run_id = rid
            return res
        return await real_fn(*args, **kwargs)
    return _wrapped
