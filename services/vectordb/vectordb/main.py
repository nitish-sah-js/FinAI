"""vectordb service on L2:8104 (docs/08). Run from services/vectordb:
    ..\\..\\.venv\\Scripts\\python -m uvicorn vectordb.main:app --host 0.0.0.0 --port 8104

Endpoints: POST /find_analogs (tool "analogs"), POST /news/index and /news/search (tool "news"), POST /embed,
GET /latency, GET /health. Tool endpoints always return a ToolResult (never raise); MOCK=1 returns the fixtures in
copilot_common/fixtures/vectordb/ without loading the model.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Request
from pydantic import BaseModel

from copilot_common.ids import counter_for
from copilot_common.models import Evidence, ToolResult
from copilot_common.service_base import create_service_app, degraded, get_run_id, mock_or, now_utc
from copilot_common.settings import get_settings

from . import __version__
from . import client as wv
from .analogs import AnalogReq, evidence_confidence, find_analogs
from .corpus import corpus_vectors, load_events
from .embed import MODEL_NAME, Embedder, model_state
from .news import NewsIndexReq, NewsSearchReq, index_news, latency_stats, record_analog_latency, search_news

logger = logging.getLogger(__name__)
PROBE_EVERY_S = 15.0


async def _prober() -> None:
    """Keeps the Weaviate breaker state fresh so requests never pay a connect attempt."""
    while True:
        try:
            await asyncio.to_thread(wv.refresh)
        except Exception as e:  # noqa: BLE001
            logger.debug("probe failed: %s", e)
        await asyncio.sleep(PROBE_EVERY_S)


@asynccontextmanager
async def lifespan(app):
    task = None
    if not get_settings().MOCK:              # MOCK=1: nothing is loaded (08 §9)
        emb = await asyncio.to_thread(Embedder)
        events = load_events()
        if events:
            await asyncio.to_thread(corpus_vectors, events, emb)       # warm the fallback cache
        task = asyncio.create_task(_prober())
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    wv.close_client()


async def deps_check() -> dict[str, str]:
    if get_settings().MOCK:
        return {"weaviate": "ok (mock)", "embedder": "ok (mock)"}
    st = model_state()
    emb = "ok" if st["status"] == "ok" else ("not loaded" if st["status"] == "not loaded"
                                             else f"hashing fallback ({st['error']})")
    return {"weaviate": await wv.weaviate_ready(), "embedder": emb,
            "corpus": "ok" if load_events() else "missing data/historical_events.json"}


app = create_service_app("vectordb", version=__version__, deps_check=deps_check, models=[MODEL_NAME],
                         lifespan=lifespan)


def _run_id(body_run_id: str | None) -> str | None:
    return get_run_id() or body_run_id or None


def _ev_id(run_id: str | None, tool: str) -> str:
    return counter_for(run_id).next(tool) if run_id else f"ev_{tool}_001"


def _chaos_header(request: Request) -> dict:
    raw = request.headers.get("x-chaos") or ""
    return {k.strip(): True for k in raw.split(",") if k.strip()}


def _fmt_pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


# ================================================================ /find_analogs
@app.post("/find_analogs")
async def find_analogs_endpoint(req: AnalogReq, request: Request):
    hdr = _chaos_header(request)
    if hdr:
        req = req.model_copy(update={"chaos": {**hdr, **req.chaos}})
    return await mock_or("vectordb", "/find_analogs", lambda: _find_analogs(req))


async def _find_analogs(req: AnalogReq) -> dict:
    t0 = time.perf_counter()
    run_id = _run_id(req.run_id)
    ev_id = _ev_id(run_id, "analogs")
    try:
        value, warnings, meta = await find_analogs(req)
    except Exception as e:  # noqa: BLE001  never raise to the caller (01 §7.4)
        logger.exception("find_analogs failed")
        ev = degraded("analogs", ev_id, f"internal_error:{type(e).__name__}", run_id=run_id, confidence=0.1,
                      value={"analogs": [], "distribution": [], "confidence": "low", "filters_relaxed": []},
                      source="vectordb")
        return ToolResult(evidence=[ev], warnings=[str(e)[:300]]).model_dump(mode="json")
    now = now_utc()
    as_of = (datetime(req.as_of.year, req.as_of.month, req.as_of.day, tzinfo=timezone.utc)
             if req.as_of else now)
    n = len(value["analogs"])
    sims = [a["similarity"] for a in value["analogs"]]
    kind = req.event_type or "event"
    summary = f"{n} {kind} analog(s)" + (f" (mean sim {sum(sims) / n:.2f})" if n else "")
    if value["distribution"]:
        d = value["distribution"][0]
        summary += (f"; {d['asset']} {d['horizon']} {d['measure']} median {_fmt_pct(d['median'])} "
                    f"(p10 {_fmt_pct(d['p10'])}, p90 {_fmt_pct(d['p90'])})")
    if meta.backend == "weaviate":
        source = f"Weaviate HistoricalEvent ({meta.n_corpus} events, {meta.embedder}, hybrid alpha {req.alpha}) + yfinance outcomes"
    else:
        source = f"numpy cosine over data/historical_events.json ({meta.n_corpus} events, {meta.embedder}) + yfinance outcomes"
    latency = int((time.perf_counter() - t0) * 1000)
    record_analog_latency(latency)
    ev = Evidence(id=ev_id, run_id=run_id, tool="analogs", value=value, summary=summary, source=source,
                  as_of=as_of, timestamp=now, freshness_s=max(0, int((now - as_of).total_seconds())),
                  confidence=evidence_confidence(value, meta), degraded=meta.degraded,
                  degraded_reason=meta.reason, latency_ms=latency, model_version="analogs_v2")
    return ToolResult(evidence=[ev], warnings=warnings).model_dump(mode="json")


# ================================================================ /news/index
@app.post("/news/index")
async def news_index_endpoint(req: NewsIndexReq):
    return await mock_or("vectordb", "/news/index", lambda: _news_index(req))


def _news_degraded(run_id, ev_id, reason, value, warnings, t0, summary) -> dict:
    ev = degraded("news", ev_id, reason, value=value, run_id=run_id, confidence=0.5, source="vectordb NewsItem (in-memory fallback)")
    ev.summary = summary
    ev.latency_ms = int((time.perf_counter() - t0) * 1000)
    return ToolResult(evidence=[ev], warnings=warnings).model_dump(mode="json")


async def _news_index(req: NewsIndexReq) -> dict:
    t0 = time.perf_counter()
    run_id = _run_id(req.run_id)
    ev_id = _ev_id(run_id, "news")
    try:
        value, reason, warnings = await index_news(req)
    except Exception as e:  # noqa: BLE001
        logger.exception("news/index failed")
        return _news_degraded(run_id, ev_id, f"internal_error:{type(e).__name__}", {"indexed": 0}, [str(e)[:300]],
                              t0, "news indexing failed")
    p95 = value["pipeline_ms"].get("p95")
    summary = f"Indexed {value['indexed']} headline(s), p95 embed+insert {p95} ms"
    if reason:
        return _news_degraded(run_id, ev_id, reason, value, warnings, t0, summary + " (in-memory, Weaviate down)")
    now = now_utc()
    ev = Evidence(id=ev_id, run_id=run_id, tool="news", value=value, summary=summary, source="vectordb NewsItem",
                  as_of=now, timestamp=now, freshness_s=0, confidence=1.0,
                  latency_ms=int((time.perf_counter() - t0) * 1000), model_version=MODEL_NAME)
    return ToolResult(evidence=[ev], warnings=warnings).model_dump(mode="json")


# ================================================================ /news/search
@app.post("/news/search")
async def news_search_endpoint(req: NewsSearchReq):
    return await mock_or("vectordb", "/news/search", lambda: _news_search(req))


async def _news_search(req: NewsSearchReq) -> dict:
    t0 = time.perf_counter()
    run_id = _run_id(req.run_id)
    ev_id = _ev_id(run_id, "news")
    try:
        items, reason, warnings = await search_news(req)
    except Exception as e:  # noqa: BLE001
        logger.exception("news/search failed")
        return _news_degraded(run_id, ev_id, f"internal_error:{type(e).__name__}", {"items": [], "n": 0},
                              [str(e)[:300]], t0, "news search failed")
    value = {"items": items, "n": len(items)}
    summary = f"{len(items)} news item(s) for '{req.query[:60]}'"
    if reason:
        return _news_degraded(run_id, ev_id, reason, value, warnings, t0, summary + " (in-memory, Weaviate down)")
    now = now_utc()
    as_of = max((datetime.fromisoformat(str(i["published_at"])) for i in items if i.get("published_at")), default=now)
    ev = Evidence(id=ev_id, run_id=run_id, tool="news", value=value, summary=summary,
                  source="vectordb NewsItem hybrid search (alpha 0.5)", as_of=as_of, timestamp=now,
                  freshness_s=max(0, int((now - as_of).total_seconds())), confidence=0.8,
                  latency_ms=int((time.perf_counter() - t0) * 1000), model_version=MODEL_NAME)
    return ToolResult(evidence=[ev], warnings=warnings).model_dump(mode="json")


# ================================================================ /embed, /latency
class EmbedReq(BaseModel):
    texts: list[str]


@app.post("/embed")
async def embed_endpoint(req: EmbedReq) -> dict:
    if get_settings().MOCK:
        return {"vectors": [[0.0] * 384 for _ in req.texts], "model": "mock", "dim": 384}
    emb = Embedder()
    vecs = await asyncio.to_thread(emb.encode, req.texts)
    return {"vectors": vecs.tolist(), "model": emb.name, "dim": int(vecs.shape[1]) if len(vecs) else 384,
            "degraded": emb.is_fallback}


@app.get("/latency")
async def latency_endpoint() -> dict:
    return latency_stats()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn
    uvicorn.run("vectordb.main:app", host="0.0.0.0", port=8104)
