"""create_service_app(): the same /health, CORS, timing, run-id and mock behaviour for every service (01 §7)."""
from __future__ import annotations

import asyncio
import contextvars
import json
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .auth import ClusterKeyMiddleware, install_httpx_hook
from fastapi.responses import JSONResponse

from .models import Evidence, Health, ToolResult
from .settings import get_settings

FIXTURES_DIR = Path(__file__).parent / "fixtures"

_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)


def get_run_id() -> str | None:
    return _run_id.get()


def set_run_id(run_id: str | None) -> contextvars.Token:
    return _run_id.set(run_id)


def load_fixture(service: str, endpoint: str) -> Any:
    """fixtures/<service>/<endpoint>.json (endpoint slashes become underscores)."""
    name = endpoint.strip("/").replace("/", "_")
    p = FIXTURES_DIR / service / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def degraded(tool: str, ev_id: str, reason: str, value: dict | None = None, source: str = "fallback",
             confidence: float | None = 0.2, run_id: str | None = None) -> Evidence:
    """Build a degraded Evidence (used when a tool fails and no cached value exists)."""
    t = now_utc()
    return Evidence(id=ev_id, run_id=run_id, tool=tool, value=value or {}, source=source, as_of=t, timestamp=t,
                    freshness_s=0, confidence=confidence, degraded=True, degraded_reason=reason,
                    summary=f"{tool} unavailable ({reason})")


async def mock_or(service: str, endpoint: str, real_fn: Callable[[], Awaitable[Any]]) -> Any:
    """MOCK=1 → fixture marked degraded/mock (after MOCK_DELAY_MS); else call real_fn."""
    s = get_settings()
    if not s.MOCK:
        return await real_fn()
    await asyncio.sleep(s.MOCK_DELAY_MS / 1000)
    data = load_fixture(service, endpoint)
    if isinstance(data, dict) and "evidence" in data:
        for ev in data["evidence"]:
            ev["degraded"] = True
            ev["degraded_reason"] = "mock"
    return data


_GPU: dict = {"t": 0.0, "v": {}}


def gpu_info(max_age_s: float = 30) -> dict:
    """First GPU's name and memory from nvidia-smi (cached); {} on machines without an NVIDIA GPU."""
    if time.time() - _GPU["t"] < max_age_s:
        return _GPU["v"]
    v: dict = {}
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=3).stdout.strip().splitlines()
        if out:
            name, used, total = [x.strip() for x in out[0].split(",")]
            v = {"name": name, "mem_used_mb": int(float(used)), "mem_total_mb": int(float(total))}
    except (OSError, ValueError, subprocess.SubprocessError):
        v = {}
    _GPU.update(t=time.time(), v=v)
    return v


def create_service_app(name: str, version: str = "0.1.0",
                       deps_check: Callable[[], Awaitable[dict[str, str]]] | None = None,
                       models: list[str] | None = None, lifespan=None) -> FastAPI:
    app = FastAPI(title=f"copilot-{name}", version=version, lifespan=lifespan)
    started = time.time()
    install_httpx_hook()                       # outgoing calls to our other services carry X-Cluster-Key
    app.add_middleware(ClusterKeyMiddleware)   # incoming calls must carry it (when CLUSTER_KEY is set)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def _timing_and_run_id(request: Request, call_next):
        token = set_run_id(request.headers.get("x-run-id"))
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            _run_id.reset(token)
        response.headers["X-Latency-Ms"] = str(int((time.perf_counter() - t0) * 1000))
        return response

    @app.exception_handler(Exception)
    async def _never_raise(request: Request, exc: Exception):
        # Tool endpoints must not raise to the caller (01 §7.4): return a degraded ToolResult with 200.
        ev = degraded(tool=name, ev_id=f"ev_{name}_000", reason=f"internal_error:{type(exc).__name__}")
        return JSONResponse(ToolResult(evidence=[ev], warnings=[str(exc)]).model_dump(mode="json"), status_code=200)

    @app.get("/health", response_model=Health)
    async def health() -> Health:
        deps: dict[str, str] = {}
        if deps_check:
            try:
                deps = await deps_check()
            except Exception as e:  # noqa: BLE001
                deps = {"deps_check": f"error: {e}"}
        status = "ok" if all(v == "ok" for v in deps.values()) else "degraded"
        return Health(service=name, status=status, version=version, mock=get_settings().MOCK,
                      host=socket.gethostname(), uptime_s=int(time.time() - started), deps=deps,
                      models=models or [], gpu=gpu_info())

    return app
