"""
vectordb/main.py
FastAPI service for Vector DB & Historical Analogs (port 8104).
"""
from __future__ import annotations
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Request
from pydantic import BaseModel

from copilot_common import create_service_app, mock_or, degraded_evidence
from copilot_common.models import Evidence, ToolResult
from copilot_common.ids import EvidenceCounter
from copilot_common.settings import settings

from contextlib import asynccontextmanager

from .analogs import AnalogReq, find_analogs
from .client import weaviate_ready, close_client
from .embed import Embedder
from .news import NewsIn, NewsSearchReq, index_news, search_news, get_latency_stats


@asynccontextmanager
async def lifespan(app):
    yield
    close_client()


app = create_service_app(
    "vectordb",
    version="0.1.0",
    deps_check=weaviate_ready,
    lifespan=lifespan,
)

_counter = EvidenceCounter("global")


# ---- POST /find_analogs ----

async def _real_find_analogs(req: AnalogReq) -> ToolResult:
    t0 = time.perf_counter()
    now = datetime.now(timezone.utc)
    ev_id = _counter.next("analogs")

    value, warnings, is_degraded = await find_analogs(req)

    latency_ms = int((time.perf_counter() - t0) * 1000)

    # Summary
    n = len(value.get("analogs", []))
    dist = value.get("distribution", [])
    if dist:
        first = dist[0]
        summary = (
            f"{n} analog(s); {first['asset']} {first['horizon']} {first['measure']} "
            f"median {first['median']:.3f} (p10 {first['p10']:.3f}, p90 {first['p90']:.3f})"
        )
    else:
        summary = f"{n} analog(s) found, no distribution computed"

    confidence = 0.8 if value.get("confidence") == "high" else (0.6 if value.get("confidence") == "medium" else 0.3)
    if value.get("filters_relaxed"):
        confidence -= 0.1 * len(value["filters_relaxed"])
    confidence = max(0.0, min(1.0, confidence))

    ev = Evidence(
        id=ev_id,
        run_id=req.run_id,
        tool="analogs",
        value=value,
        summary=summary,
        source="Weaviate HistoricalEvent (bge-small-en-v1.5) + yfinance outcomes",
        as_of=now,
        timestamp=now,
        confidence=confidence,
        degraded=is_degraded,
        degraded_reason="weaviate_down_numpy_fallback" if is_degraded else None,
        latency_ms=latency_ms,
        model_version="analogs_v1",
    )
    return ToolResult(evidence=[ev], warnings=warnings)


_find_analogs_handler = mock_or("vectordb", "find_analogs", _real_find_analogs)


@app.post("/find_analogs", response_model=ToolResult)
async def find_analogs_endpoint(req: AnalogReq) -> ToolResult:
    return await _find_analogs_handler(req)


# ---- POST /news/index ----

class NewsIndexReq(BaseModel):
    items: list[NewsIn]


async def _real_news_index(req: NewsIndexReq) -> ToolResult:
    t0 = time.perf_counter()
    now = datetime.now(timezone.utc)
    ev_id = _counter.next("news")

    stats = await index_news(req.items)
    latency_ms = int((time.perf_counter() - t0) * 1000)

    ev = Evidence(
        id=ev_id,
        tool="news",
        value=stats,
        summary=f"Indexed {stats['indexed']} headlines, p95 embed+insert {stats['pipeline_ms'].get('p95')} ms",
        source="vectordb NewsItem",
        as_of=now,
        timestamp=now,
        confidence=1.0,
        degraded=False,
        latency_ms=latency_ms,
    )
    return ToolResult(evidence=[ev])


_news_index_handler = mock_or("vectordb", "news_index", _real_news_index)


@app.post("/news/index", response_model=ToolResult)
async def news_index_endpoint(req: NewsIndexReq) -> ToolResult:
    return await _news_index_handler(req)


# ---- POST /news/search ----

async def _real_news_search(req: NewsSearchReq) -> ToolResult:
    t0 = time.perf_counter()
    now = datetime.now(timezone.utc)
    ev_id = _counter.next("news")

    items = await search_news(req)
    latency_ms = int((time.perf_counter() - t0) * 1000)

    ev = Evidence(
        id=ev_id,
        tool="news",
        value={"items": items, "n": len(items)},
        summary=f"{len(items)} news items matching '{req.query}'",
        source="vectordb NewsItem hybrid search",
        as_of=now,
        timestamp=now,
        confidence=1.0,
        latency_ms=latency_ms,
    )
    return ToolResult(evidence=[ev])


_news_search_handler = mock_or("vectordb", "news_search", _real_news_search)


@app.post("/news/search", response_model=ToolResult)
async def news_search_endpoint(req: NewsSearchReq) -> ToolResult:
    return await _news_search_handler(req)


# ---- POST /embed ----

class EmbedReq(BaseModel):
    texts: list[str]


@app.post("/embed")
async def embed_endpoint(req: EmbedReq) -> dict:
    if settings.mock:
        return {"vectors": [[0.0] * 384 for _ in req.texts]}
    embedder = Embedder()
    vecs = embedder.encode(req.texts)
    return {"vectors": vecs.tolist()}


# ---- GET /latency ----

@app.get("/latency")
async def latency_endpoint() -> dict:
    return get_latency_stats()


# ---- Entry point ----

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("vectordb.main:app", host="0.0.0.0", port=8104, reload=False)
