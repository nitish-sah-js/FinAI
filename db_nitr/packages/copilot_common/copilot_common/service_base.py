from __future__ import annotations
import asyncio
import json
import time
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .models import Evidence, Health, ToolResult
from .settings import settings

_run_id_var: ContextVar[str] = ContextVar("run_id", default="")


def get_run_id() -> str:
    return _run_id_var.get()


def _fixture_path(service: str, endpoint: str) -> Path:
    """Look for fixture in the service folder, then in copilot_common fixtures."""
    candidates = [
        Path("fixtures") / service / f"{endpoint}.json",
        Path(__file__).parent / "fixtures" / service / f"{endpoint}.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]   # will raise FileNotFoundError on read if missing


def mock_or(
    service: str,
    endpoint: str,
    real_fn: Callable[..., Awaitable[ToolResult]],
):
    """
    Return a wrapper: when MOCK=1, load the fixture and return it (after MOCK_DELAY_MS).
    Otherwise call real_fn.
    """
    async def _wrapped(*args, **kwargs) -> ToolResult:
        if settings.mock:
            await asyncio.sleep(settings.mock_delay_ms / 1000)
            fp = _fixture_path(service, endpoint)
            raw = json.loads(fp.read_text())
            # Tag each evidence as degraded/mock
            for ev in raw.get("evidence", []):
                ev["degraded"] = True
                ev["degraded_reason"] = "mock"
            return ToolResult.model_validate(raw)
        return await real_fn(*args, **kwargs)
    return _wrapped


def degraded_evidence(
    *,
    ev_id: str,
    tool: str,
    reason: str,
    last_value: dict | None = None,
    confidence: float = 0.3,
) -> Evidence:
    now = datetime.now(timezone.utc)
    return Evidence(
        id=ev_id,
        tool=tool,
        value=last_value or {},
        source="degraded",
        as_of=now,
        timestamp=now,
        confidence=confidence,
        degraded=True,
        degraded_reason=reason,
    )


def create_service_app(
    name: str,
    version: str = "0.1.0",
    deps_check: Callable[[], str] | None = None,
    lifespan: Any = None,
) -> FastAPI:
    """
    Standard service factory:
    - GET /health
    - CORS *
    - X-Latency-Ms middleware
    - X-Run-Id propagation
    - Global exception → ToolResult(degraded) handler
    """
    kwargs = {"title": name, "version": version}
    if lifespan is not None:
        kwargs["lifespan"] = lifespan
    app = FastAPI(**kwargs)
    _start = time.time()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def timing_and_run_id(request: Request, call_next):
        run_id = request.headers.get("X-Run-Id", "")
        token = _run_id_var.set(run_id)
        t0 = time.perf_counter()
        response: Response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        response.headers["X-Latency-Ms"] = str(elapsed_ms)
        if run_id:
            response.headers["X-Run-Id"] = run_id
        _run_id_var.reset(token)
        return response

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        now = datetime.now(timezone.utc)
        ev = Evidence(
            id="ev_error_000",
            tool="error",
            value={"error": str(exc)},
            source=name,
            as_of=now,
            timestamp=now,
            degraded=True,
            degraded_reason=f"unhandled_exception: {type(exc).__name__}",
            confidence=0.0,
        )
        result = ToolResult(evidence=[ev], warnings=[traceback.format_exc()])
        return JSONResponse(content=result.model_dump(mode="json"), status_code=200)

    @app.get("/health", response_model=Health)
    async def health() -> Health:
        deps: dict[str, str] = {}
        if deps_check is not None:
            try:
                deps_check()
                deps["primary"] = "ok"
            except Exception as e:
                deps["primary"] = f"down: {e}"

        status = "ok"
        if deps and any("down" in v for v in deps.values()):
            status = "degraded"

        return Health(
            service=name,
            status=status,
            version=version,
            mock=settings.mock,
            uptime_s=int(time.time() - _start),
            deps=deps,
        )

    return app
