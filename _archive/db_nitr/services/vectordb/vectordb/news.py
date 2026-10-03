"""
vectordb/news.py
News indexing and search with latency tracking.
"""
from __future__ import annotations
import logging
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np
from pydantic import BaseModel

from .embed import Embedder, news_text

logger = logging.getLogger(__name__)

# Ring buffer size for latency stats
_RING_SIZE = 1000

# Latency ring buffers (pipeline_ms and end_to_end_s)
_pipeline_ms_buf: deque[float] = deque(maxlen=_RING_SIZE)
_end_to_end_s_buf: deque[float] = deque(maxlen=_RING_SIZE)


# ---- Input models ----

class NewsIn(BaseModel):
    news_id: str
    title: str
    summary: str = ""
    source: str = ""
    url: str = ""
    published_at: datetime
    tickers: list[str] = []
    sentiment_label: Optional[str] = None
    sentiment_score: Optional[float] = None
    ingested_at: datetime


class NewsSearchReq(BaseModel):
    query: str
    tickers: list[str] = []
    since_hours: int = 48
    k: int = 10


# ---- Internal helpers ----

def _pct(buf: deque, q: float) -> float | None:
    if not buf:
        return None
    return float(np.percentile(list(buf), q * 100))


def get_latency_stats() -> dict:
    return {
        "pipeline_ms": {
            "p50": _pct(_pipeline_ms_buf, 0.5),
            "p95": _pct(_pipeline_ms_buf, 0.95),
            "n": len(_pipeline_ms_buf),
        },
        "end_to_end_s": {
            "p50": _pct(_end_to_end_s_buf, 0.5),
            "p95": _pct(_end_to_end_s_buf, 0.95),
        },
    }


# ---- Index news ----

async def index_news(items: list[NewsIn]) -> dict:
    """
    Embed and insert news items into Weaviate NewsItem collection.
    Returns stats dict.
    """
    from weaviate.util import generate_uuid5
    from .client import get_client
    from .schema import NEWS_ITEM_COLLECTION

    client = get_client()
    coll = client.collections.get(NEWS_ITEM_COLLECTION)
    embedder = Embedder()

    # Dedupe: collect UUIDs that already exist
    all_uuids = {str(generate_uuid5(item.news_id)) for item in items}
    existing = set()
    for uid in all_uuids:
        try:
            obj = coll.query.fetch_object_by_id(uid)
            if obj is not None:
                existing.add(uid)
        except Exception:
            pass

    to_insert = [
        item for item in items
        if str(generate_uuid5(item.news_id)) not in existing
    ]

    skipped = len(items) - len(to_insert)
    if not to_insert:
        return {
            "indexed": 0,
            "skipped_duplicates": skipped,
            "pipeline_ms": get_latency_stats()["pipeline_ms"],
            "end_to_end_s": get_latency_stats()["end_to_end_s"],
        }

    texts = [news_text(i.model_dump()) for i in to_insert]
    vecs = embedder.encode(texts)

    now = datetime.now(timezone.utc)

    data_objects = []
    for i, (item, vec) in enumerate(zip(to_insert, vecs)):
        uid = str(generate_uuid5(item.news_id))
        props = {
            "news_id": item.news_id,
            "title": item.title,
            "summary": item.summary,
            "source": item.source,
            "url": item.url,
            "published_at": item.published_at.isoformat(),
            "tickers": item.tickers,
            "sentiment_label": item.sentiment_label or "",
            "sentiment_score": item.sentiment_score or 0.0,
            "ingested_at": item.ingested_at.isoformat(),
            "indexed_at": now.isoformat(),
        }
        data_objects.append({"uuid": uid, "properties": props, "vector": vec.tolist()})

    from weaviate.classes.data import DataObject
    objs = [DataObject(properties=d["properties"], uuid=d["uuid"], vector=d["vector"])
            for d in data_objects]
    coll.data.insert_many(objs)

    # Record latencies
    for item in to_insert:
        pipeline_ms = (now - item.ingested_at).total_seconds() * 1000
        e2e_s = (now - item.published_at).total_seconds()
        _pipeline_ms_buf.append(pipeline_ms)
        _end_to_end_s_buf.append(e2e_s)

    stats = get_latency_stats()
    return {
        "indexed": len(to_insert),
        "skipped_duplicates": skipped,
        "pipeline_ms": stats["pipeline_ms"],
        "end_to_end_s": stats["end_to_end_s"],
    }


# ---- Search news ----

async def search_news(req: NewsSearchReq) -> list[dict]:
    """Hybrid search over NewsItem collection."""
    from weaviate.classes.query import Filter, MetadataQuery
    from .client import get_client
    from .schema import NEWS_ITEM_COLLECTION

    client = get_client()
    coll = client.collections.get(NEWS_ITEM_COLLECTION)
    embedder = Embedder()

    qvec = embedder.encode_one(req.query).tolist()

    since_dt = datetime.now(timezone.utc) - timedelta(hours=req.since_hours)

    filters = [Filter.by_property("published_at").greater_or_equal(since_dt.isoformat())]
    if req.tickers:
        ticker_filters = [Filter.by_property("tickers").contains_any([t]) for t in req.tickers]
        combined_ticker = ticker_filters[0]
        for tf in ticker_filters[1:]:
            combined_ticker = combined_ticker | tf
        filters.append(combined_ticker)

    combined = filters[0]
    for f in filters[1:]:
        combined = combined & f

    result = coll.query.hybrid(
        query=req.query,
        vector=qvec,
        alpha=0.5,
        limit=req.k,
        filters=combined,
        return_metadata=MetadataQuery(score=True),
    )

    items = []
    for obj in result.objects:
        items.append({
            "news_id": obj.properties.get("news_id"),
            "title": obj.properties.get("title"),
            "summary": obj.properties.get("summary"),
            "source": obj.properties.get("source"),
            "url": obj.properties.get("url"),
            "published_at": obj.properties.get("published_at"),
            "tickers": obj.properties.get("tickers", []),
            "sentiment_label": obj.properties.get("sentiment_label"),
            "sentiment_score": obj.properties.get("sentiment_score"),
            "score": obj.metadata.score if obj.metadata else None,
        })
    return items
