"""Orchestrator API on L1:8000 (04 §8). Run from services/:  uvicorn orchestrator.app:app --host 0.0.0.0 --port 8000"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Body, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse

from copilot_common.ids import new_run_id
from copilot_common.models import Portfolio, QueryAccepted, QueryRequest, ToolResult
from copilot_common.service_base import create_service_app
from copilot_common.settings import get_settings
from copilot_llm import SESSION, llm

from . import graph as G
from . import portfolio as portfolios
from . import tools_client as tools
from .events import bus
from .ledger import ledger
from .nodes.explain import explain_run

HEALTH_SERVICES = {"quant": ("QUANT_URL", "L2"), "sentiment": ("SENTIMENT_URL", "L2"), "agri": ("AGRI_URL", "L2"),
                   "vectordb": ("VECTOR_URL", "L2"), "ingestion": ("INGEST_URL", "L3"), "monitor": ("MONITOR_URL", "L3")}
_tasks: set[asyncio.Task] = set()


async def deps_check() -> dict[str, str]:
    s = get_settings()
    deps = {"ledger": "ok"}
    if s.MOCK:
        deps["ollama_L1"] = "ok"
        return deps
    try:
        async with httpx.AsyncClient(timeout=1.5) as c:
            r = await c.get(f"{s.OLLAMA_L1.rstrip('/')}/api/tags")
            deps["ollama_L1"] = "ok" if r.status_code == 200 else f"http {r.status_code}"
    except httpx.HTTPError as e:
        deps["ollama_L1"] = f"down ({type(e).__name__})"
    if not llm.is_warm():
        deps["llm_warmup"] = f"{llm.warm['state']}: intent uses keyword rules until the model is loaded"
    return deps


@asynccontextmanager
async def lifespan(app):
    portfolios.ensure_demo_csv()
    saver_cm = None
    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        saver_cm = AsyncSqliteSaver.from_conn_string(str(get_settings().data_dir / "checkpoints.db"))
        saver = await saver_cm.__aenter__()
        G.set_graph(G.build_graph(saver))
    except Exception:  # noqa: BLE001  fall back to in-memory checkpoints (resume works until restart)
        saver_cm = None
        G.set_graph(G.build_graph())
    _spawn(llm.warmup())             # load local models into VRAM in the background (cold load ≈ 40 s)
    if _paper_scheduler is not None:
        _spawn(_paper_scheduler())   # paper-trading marks every 15 min in market hours (12 §B3)
    yield
    for t in list(_tasks):
        t.cancel()
    if saver_cm is not None:
        await saver_cm.__aexit__(None, None, None)
    await ledger.close()


def _spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


app = create_service_app("orchestrator", deps_check=deps_check, models=[get_settings().OLLAMA_MODEL_L1], lifespan=lifespan)


# ---------------- queries & runs ----------------
@app.post("/query", response_model=QueryAccepted)
async def query(req: QueryRequest, request: Request) -> QueryAccepted:
    run_id = new_run_id()
    await ledger.create_run(run_id, req.query, req.model_dump(mode="json"))
    bus.start_run(run_id)
    _spawn(G.run_graph(req, run_id))
    base = str(request.base_url).rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
    return QueryAccepted(run_id=run_id, ws_url=f"{base}/ws/{run_id}")


@app.websocket("/ws/activity")
async def ws_activity(ws: WebSocket):
    await ws.accept()
    try:
        async for msg in bus.subscribe_activity():
            await ws.send_json(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass


@app.websocket("/ws/{run_id}")
async def ws_run(ws: WebSocket, run_id: str):
    await ws.accept()
    try:
        async for msg in bus.subscribe(run_id):
            await ws.send_json(msg)
        await ws.close()
    except (WebSocketDisconnect, RuntimeError):
        pass


@app.get("/runs")
async def runs(limit: int = 20) -> list[dict]:
    return await ledger.list_runs(min(max(limit, 1), 200))


async def _run_or_404(run_id: str) -> dict:
    run = await ledger.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"unknown run {run_id}")
    return run


@app.get("/runs/{run_id}")
async def run_detail(run_id: str) -> dict:
    run = await _run_or_404(run_id)
    events = [m["data"] for _, m in await ledger.get_events(run_id) if m.get("type") == "event"]
    return {"run_id": run_id, "status": run["status"], "query": run["query"], "final": run["final"], "events": events}


@app.get("/runs/{run_id}/events.jsonl", response_class=PlainTextResponse)
async def run_events_jsonl(run_id: str) -> str:
    await _run_or_404(run_id)
    return "\n".join(json.dumps({"t_ms": t, "msg": m}, ensure_ascii=False) for t, m in await ledger.get_events(run_id)) + "\n"


@app.get("/runs/{run_id}/explain")
async def run_explain(run_id: str) -> dict:
    run = await _run_or_404(run_id)
    state = {"run_id": f"explain_{run_id}", "request": {"query": f"explain {run_id}", **(run.get("request") or {})}}
    out = await explain_run(state, run_id)
    ex = out["explanation"]
    return {"run_id": run_id, "explanation_markdown": ex["explanation_markdown"], "steps": ex["steps"]}


@app.post("/runs/{run_id}/resume")
async def run_resume(run_id: str) -> dict:
    await _run_or_404(run_id)
    _spawn(G.resume_run(run_id))
    return {"run_id": run_id, "status": "resuming"}


# ---------------- LLM ----------------
@app.get("/llm/quota")
async def llm_quota() -> dict:
    snap = llm.quota.snapshot()
    return {"mode": get_settings().LLM_MODE, "quota": snap, "session": SESSION.to_dict(snap)}


@app.get("/llm/warmup")
async def llm_warmup() -> dict:
    """Warm-up state for the UI: 'warming' until every installed local model has answered once, then 'ready'.
    While it is not ready, parse_intent uses keyword rules instead of waiting on a loading model."""
    w = llm.warm
    return {"state": "ready" if llm.is_warm() else w["state"], "models": w["models"],
            "started_at": w["started_at"], "finished_at": w["finished_at"]}


@app.get("/llm/health")
async def llm_health() -> dict:
    return await llm.health()


# ---------------- health of every service ----------------
@app.get("/health/all")
async def health_all() -> dict:
    s = get_settings()

    async def one(name: str, attr: str, host: str):
        url = getattr(s, attr).rstrip("/") + "/health"
        try:
            async with httpx.AsyncClient(timeout=2) as c:
                r = await c.get(url)
                return name, {**r.json(), "laptop": host, "url": url}
        except (httpx.HTTPError, ValueError) as e:
            return name, {"service": name, "status": "down", "laptop": host, "url": url, "error": type(e).__name__,
                          "hint": "check firewall / 0.0.0.0 / same Wi-Fi"}

    results = dict(await asyncio.gather(*(one(n, a, h) for n, (a, h) in HEALTH_SERVICES.items())))
    results["orchestrator"] = {"service": "orchestrator", "status": "ok", "laptop": "L1", "mock": s.MOCK,
                               "deps": await deps_check()}
    results["llm"] = await llm.health()
    return results


# ---------------- portfolio ----------------
@app.get("/portfolio", response_model=Portfolio)
async def get_portfolio(id: str = "demo") -> Portfolio:
    return await get_portfolio_by_id(id)


@app.get("/portfolio/{portfolio_id}", response_model=Portfolio)
async def get_portfolio_by_id(portfolio_id: str) -> Portfolio:
    try:
        return portfolios.load(portfolio_id)
    except KeyError:
        raise HTTPException(404, f"unknown portfolio {portfolio_id}")


@app.post("/portfolio", response_model=Portfolio)
async def save_portfolio(p: Portfolio) -> Portfolio:
    return portfolios.save(p)


@app.post("/portfolio/upload")
async def upload_portfolio(file: UploadFile = File(...), portfolio_id: str = "demo") -> dict:
    text = (await file.read()).decode("utf-8-sig")
    p, warnings = portfolios.parse_csv(text, portfolio_id)
    if not p.holdings:
        raise HTTPException(400, "no holdings found; expected columns ticker,qty[,avg_price,sector]")
    return {"portfolio": portfolios.save(p).model_dump(mode="json"), "warnings": warnings}


# ---------------- what-if proxy, backtest ----------------
@app.post("/tools/scenario", response_model=ToolResult)
async def tools_scenario(body: dict = Body(...)) -> ToolResult:
    pf = body.get("portfolio") or portfolios.load(body.get("portfolio_id", "demo")).model_dump(mode="json")
    shocks = body.get("shocks") or {}
    return await tools.scenario(pf, shocks, as_of=body.get("as_of"), run_id=f"whatif_{new_run_id()[4:]}",
                                chaos=body.get("chaos") or {}, timeout_s=10)


@app.get("/backtest/scoreboard")
async def backtest_scoreboard():
    """Real scoreboard written by `python -m backtest.run_backtest`; MOCK=1 serves the 12 §A8 example fixture."""
    p: Path = get_settings().data_dir / "backtest" / "scoreboard.json"
    if not p.is_file() and get_settings().MOCK:
        from copilot_common.service_base import load_fixture
        return JSONResponse(load_fixture("orchestrator", "scoreboard"))
    if not p.is_file():
        return JSONResponse({"status": "not_run"})
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")))


# ---------------- paper trading (doc 12) is mounted when that module exists ----------------
try:
    from .paper.router import router as paper_router  # type: ignore[import-not-found]
    from .paper.scheduler import run_scheduler as _paper_scheduler
    app.include_router(paper_router, prefix="/paper")
except ImportError:
    _paper_scheduler = None
