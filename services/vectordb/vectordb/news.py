"""News indexing and search (08 §7) with the latency ring buffers.

Weaviate up   → embed batch, insert_many with uuid5(news_id), indexed_at stamped AFTER insert returns, per-object
                errors checked (failed objects are not counted and get no latency sample).
Weaviate down → the same items go into a bounded in-memory store (searchable with numpy cosine) and the evidence is
                degraded ("weaviate_down_memory_fallback"); nothing is lost for the current process.

The Weaviate branch is UNTESTED against a live server (Docker was down during integration).
"""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections import OrderedDict, deque
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from . import client as wv
from .embed import Embedder, news_text

logger = logging.getLogger(__name__)

RING = 1000
MEMORY_MAX = 5000
_pipeline_ms: deque[float] = deque(maxlen=RING)
_end_to_end_s: deque[float] = deque(maxlen=RING)
_analog_ms: deque[float] = deque(maxlen=RING)
_mem_lock = threading.Lock()
_memory: "OrderedDict[str, tuple[dict, np.ndarray]]" = OrderedDict()


class NewsIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    news_id: str
    title: str
    summary: str = ""
    source: str = ""
    url: str = ""
    published_at: datetime
    tickers: list[str] = []
    sentiment_label: str | None = None
    sentiment_score: float | None = None
    ingested_at: datetime | None = None          # default: arrival time at this service


class NewsIndexReq(BaseModel):
    model_config = ConfigDict(extra="ignore")
    items: list[NewsIn]
    run_id: str | None = None


class NewsSearchReq(BaseModel):
    model_config = ConfigDict(extra="ignore")
    query: str
    tickers: list[str] = []
    since_hours: float = 48
    k: int = Field(10, ge=1, le=100)
    limit: int | None = None                     # alias some callers send
    as_of: date | None = None
    run_id: str | None = None


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _pct(buf, q: float) -> float | None:
    return None if not buf else round(float(np.percentile(list(buf), q)), 1)


def latency_stats() -> dict:
    return {"pipeline_ms": {"p50": _pct(_pipeline_ms, 50), "p95": _pct(_pipeline_ms, 95), "n": len(_pipeline_ms)},
            "end_to_end_s": {"p50": _pct(_end_to_end_s, 50), "p95": _pct(_end_to_end_s, 95), "n": len(_end_to_end_s)},
            "find_analogs_ms": {"p50": _pct(_analog_ms, 50), "p95": _pct(_analog_ms, 95), "n": len(_analog_ms)}}


def record_analog_latency(ms: float) -> None:
    _analog_ms.append(ms)


def news_uuid(news_id: str) -> str:
    try:
        from weaviate.util import generate_uuid5
        return str(generate_uuid5(news_id))
    except ImportError:  # pragma: no cover
        return str(uuid.uuid5(uuid.NAMESPACE_URL, news_id))


def _props(item: NewsIn, ingested: datetime, indexed: datetime | None) -> dict:
    p: dict[str, Any] = {"news_id": item.news_id, "title": item.title, "summary": item.summary, "source": item.source,
                         "url": item.url, "published_at": _utc(item.published_at), "tickers": item.tickers,
                         "ingested_at": ingested}
    if item.sentiment_label is not None:          # None stays None (property omitted → null), never "" / 0.0
        p["sentiment_label"] = item.sentiment_label
    if item.sentiment_score is not None:
        p["sentiment_score"] = float(item.sentiment_score)
    if indexed is not None:
        p["indexed_at"] = indexed
    return p


def _record(items: list[NewsIn], ingested: dict[str, datetime], indexed_at: datetime) -> None:
    for it in items:
        _pipeline_ms.append((indexed_at - ingested[it.news_id]).total_seconds() * 1000)
        _end_to_end_s.append((indexed_at - _utc(it.published_at)).total_seconds())


def _dedupe_batch(items: list[NewsIn]) -> tuple[list[NewsIn], int]:
    seen, out = set(), []
    for it in items:
        if it.news_id in seen:
            continue
        seen.add(it.news_id)
        out.append(it)
    return out, len(items) - len(out)


def _index_weaviate(items: list[NewsIn], vecs: np.ndarray, ingested: dict[str, datetime]) -> dict:
    from weaviate.classes.data import DataObject
    from weaviate.classes.query import Filter
    from .schema import NEWS_ITEM_COLLECTION, create_collections

    c = wv.get_client()
    if not c.collections.exists(NEWS_ITEM_COLLECTION):
        create_collections(c, reset=False)
    coll = c.collections.get(NEWS_ITEM_COLLECTION)
    uuids = {it.news_id: news_uuid(it.news_id) for it in items}
    existing = set()
    res = coll.query.fetch_objects(filters=Filter.by_id().contains_any(list(uuids.values())), limit=len(uuids))
    existing = {str(o.uuid) for o in res.objects}
    todo = [(it, v) for it, v in zip(items, vecs) if uuids[it.news_id] not in existing]
    if not todo:
        return {"indexed": 0, "skipped_duplicates": len(items), "failed": 0, "errors": []}
    objs = [DataObject(properties=_props(it, ingested[it.news_id], None), uuid=uuids[it.news_id], vector=v.tolist())
            for it, v in todo]
    r = coll.data.insert_many(objs)
    indexed_at = datetime.now(timezone.utc)                  # AFTER the insert returned
    errors = getattr(r, "errors", {}) or {}
    ok = [it for i, (it, _) in enumerate(todo) if i not in errors]
    if ok:                                                   # stamp indexed_at on the stored objects
        try:
            for it in ok:
                coll.data.update(uuid=uuids[it.news_id], properties={"indexed_at": indexed_at})
        except Exception as e:  # noqa: BLE001
            logger.warning("could not stamp indexed_at: %s", e)
    _record(ok, ingested, indexed_at)
    return {"indexed": len(ok), "skipped_duplicates": len(items) - len(todo), "failed": len(errors),
            "errors": [str(getattr(e, "message", e))[:200] for e in list(errors.values())[:5]]}


def _index_memory(items: list[NewsIn], vecs: np.ndarray, ingested: dict[str, datetime]) -> dict:
    with _mem_lock:
        todo = [(it, v) for it, v in zip(items, vecs) if it.news_id not in _memory]
        indexed_at = datetime.now(timezone.utc)
        for it, v in todo:
            _memory[it.news_id] = (_props(it, ingested[it.news_id], indexed_at), v)
        while len(_memory) > MEMORY_MAX:
            _memory.popitem(last=False)
    _record([it for it, _ in todo], ingested, indexed_at)
    return {"indexed": len(todo), "skipped_duplicates": len(items) - len(todo), "failed": 0, "errors": []}


async def index_news(req: NewsIndexReq, embedder: Embedder | None = None) -> tuple[dict, str | None, list[str]]:
    """(value, degraded_reason | None, warnings)."""
    arrival = datetime.now(timezone.utc)
    items, dup_in_batch = _dedupe_batch(req.items)
    warnings = [f"{dup_in_batch} duplicate news_id(s) inside the batch dropped"] if dup_in_batch else []
    ingested = {it.news_id: _utc(it.ingested_at) if it.ingested_at else arrival for it in items}
    if not items:
        return {"indexed": 0, "skipped_duplicates": dup_in_batch, **latency_stats()}, None, warnings
    embedder = embedder or Embedder()
    vecs = await asyncio.to_thread(embedder.encode, [news_text(it.model_dump()) for it in items])
    reason = None
    if embedder.is_fallback:
        warnings.append("bge-small-en-v1.5 unavailable: hashing vectors kept in memory only (not written to Weaviate)")
        reason = "embedder_unavailable_memory_fallback"
    elif wv.is_available():
        try:
            out = await asyncio.to_thread(_index_weaviate, items, vecs, ingested)
            if out["failed"]:
                warnings.append(f"{out['failed']} insert(s) failed: {out['errors']}")
            out["skipped_duplicates"] += dup_in_batch
            out.pop("errors", None)
            return {**out, "backend": "weaviate", **_lat()}, None, warnings
        except Exception as e:  # noqa: BLE001
            wv.report_failure(e)
            warnings.append(f"weaviate insert failed ({type(e).__name__}); kept in memory")
    reason = reason or "weaviate_down_memory_fallback"
    out = await asyncio.to_thread(_index_memory, items, vecs, ingested)
    out["skipped_duplicates"] += dup_in_batch
    out.pop("errors", None)
    return {**out, "backend": "memory", **_lat()}, reason, warnings


def _lat() -> dict:
    s = latency_stats()
    return {"pipeline_ms": s["pipeline_ms"], "end_to_end_s": s["end_to_end_s"]}


def _since(req: NewsSearchReq) -> tuple[datetime, datetime | None]:
    upper = datetime.combine(req.as_of, dtime(23, 59, 59), tzinfo=timezone.utc) if req.as_of else None
    anchor = upper or datetime.now(timezone.utc)
    return anchor - timedelta(hours=req.since_hours), upper


def _search_weaviate(req: NewsSearchReq, qvec: np.ndarray, k: int) -> list[dict]:
    from weaviate.classes.query import Filter, MetadataQuery
    from .schema import NEWS_ITEM_COLLECTION

    coll = wv.get_client().collections.get(NEWS_ITEM_COLLECTION)
    lo, hi = _since(req)
    f = Filter.by_property("published_at").greater_or_equal(lo)
    if hi is not None:
        f = f & Filter.by_property("published_at").less_or_equal(hi)
    if req.tickers:
        f = f & Filter.by_property("tickers").contains_any(req.tickers)
    res = coll.query.hybrid(query=req.query, vector=qvec.tolist(), alpha=0.5, limit=k, filters=f,
                            return_metadata=MetadataQuery(score=True))
    return [_public(dict(o.properties or {}), getattr(o.metadata, "score", None)) for o in res.objects]


def _public(p: dict, score: float | None) -> dict:
    """Search result shape: ISO timestamps, sentiment keys always present (null when unknown)."""
    for key in ("published_at", "ingested_at", "indexed_at"):
        if isinstance(p.get(key), datetime):
            p[key] = p[key].isoformat()
    p.setdefault("sentiment_label", None)
    p.setdefault("sentiment_score", None)
    p["score"] = None if score is None else round(float(score), 4)
    return p


def _search_memory(req: NewsSearchReq, qvec: np.ndarray, k: int) -> list[dict]:
    lo, hi = _since(req)
    want = set(req.tickers)
    with _mem_lock:
        rows = list(_memory.values())
    cands = [(p, v) for p, v in rows if p["published_at"] >= lo and (hi is None or p["published_at"] <= hi)
             and (not want or want & set(p.get("tickers") or []))]
    if not cands:
        return []
    sims = np.stack([v for _, v in cands]) @ qvec
    order = np.argsort(-sims)[:k]
    return [_public(dict(cands[i][0]), float(sims[i])) for i in order]


async def search_news(req: NewsSearchReq, embedder: Embedder | None = None) -> tuple[list[dict], str | None, list[str]]:
    k = req.limit or req.k
    embedder = embedder or Embedder()
    qvec = await asyncio.to_thread(embedder.encode_one, req.query)
    warnings: list[str] = []
    if not embedder.is_fallback and wv.is_available():
        try:
            return await asyncio.to_thread(_search_weaviate, req, qvec, k), None, warnings
        except Exception as e:  # noqa: BLE001
            wv.report_failure(e)
            warnings.append(f"weaviate search failed ({type(e).__name__}); searched the in-memory store")
    items = await asyncio.to_thread(_search_memory, req, qvec, k)
    warnings.append(f"Weaviate unavailable: searched {len(_memory)} item(s) indexed in this process only")
    return items, ("embedder_unavailable_memory_fallback" if embedder.is_fallback else "weaviate_down_memory_fallback"), warnings


def clear_memory() -> None:
    with _mem_lock:
        _memory.clear()
    _pipeline_ms.clear()
    _end_to_end_s.clear()
