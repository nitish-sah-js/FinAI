"""data/historical_events.json → embed (bge-small) → Weaviate HistoricalEvent (08 §8 step 6).

Used by scripts/seed_weaviate.py and by the service itself: when Weaviate comes up with fewer events than the
corpus (fresh Docker volume on a new L2 laptop), the background prober seeds it, so analog search runs on Weaviate
instead of silently staying on the numpy fallback. Idempotent: uuid = generate_uuid5(event_id).
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _rfc3339(d: str | None) -> datetime | None:
    if not d:
        return None
    x = date.fromisoformat(str(d)[:10])
    return datetime(x.year, x.month, x.day, tzinfo=timezone.utc)


def to_properties(e: dict, text: str) -> dict:
    p = {
        "event_id": e["event_id"], "title": e.get("title", ""), "event_type": e["event_type"],
        "region": e.get("region", ""), "country": e.get("country", ""),
        "start_date": _rfc3339(e.get("start_date")), "end_date": _rfc3339(e.get("end_date")),
        "event_date": _rfc3339(e["event_date"]),
        "severity_value": float(e.get("severity_value") or 0.0), "severity_unit": e.get("severity_unit", ""),
        "severity_norm": float(e.get("severity_norm") or 0.0), "description": e.get("description", ""),
        "mechanism": e.get("mechanism", ""), "affected_assets": e.get("affected_assets", []),
        "tickers": e.get("tickers", []), "outcomes_json": json.dumps(e.get("outcomes", [])),
        "split": e["split"], "source": e.get("source", ""), "source_url": e.get("source_url", ""),
        "embedding_text": text,
    }
    return {k: v for k, v in p.items() if v is not None}


def seeded_count(client) -> int:
    """Events stored in Weaviate (0 when the collection does not exist yet)."""
    from .schema import HISTORICAL_EVENT_COLLECTION
    if not client.collections.exists(HISTORICAL_EVENT_COLLECTION):
        return 0
    return client.collections.get(HISTORICAL_EVENT_COLLECTION).aggregate.over_all(total_count=True).total_count or 0


def seed(events: list[dict], emb, client, reset: bool = False) -> tuple[int, int]:
    """Insert (or replace) every event. Returns (inserted, errors). Refuses hashing vectors."""
    from weaviate.classes.data import DataObject
    from weaviate.util import generate_uuid5

    from .embed import event_text
    from .schema import HISTORICAL_EVENT_COLLECTION, create_collections

    if emb.is_fallback:
        raise RuntimeError("bge-small-en-v1.5 not available: refusing to seed hashing vectors")
    texts = [e.get("embedding_text") or event_text(e) for e in events]
    vecs = emb.encode(texts)
    create_collections(client, reset=reset)
    coll = client.collections.get(HISTORICAL_EVENT_COLLECTION)
    objs = [DataObject(properties=to_properties(e, t), uuid=generate_uuid5(e["event_id"]), vector=v.tolist())
            for e, t, v in zip(events, texts, vecs)]
    existing = {str(o.uuid) for o in coll.iterator(return_properties=[])}
    for o in objs:                                   # idempotent: replace what is already there
        if str(o.uuid) in existing:
            coll.data.delete_by_id(o.uuid)
    res = coll.data.insert_many(objs)
    errors = getattr(res, "errors", {}) or {}
    for i, err in list(errors.items())[:10]:
        logger.error("insert failed for %s: %s", events[i]["event_id"], getattr(err, "message", err))
    return len(objs) - len(errors), len(errors)


def ensure_seeded(events: list[dict], emb) -> str:
    """Seed Weaviate when it holds fewer events than the corpus. Returns a short status for /health."""
    from .client import get_client
    if not events:
        return "no corpus"
    client = get_client()
    have = seeded_count(client)
    if have >= len(events):
        return "ok"
    ok, bad = seed(events, emb, client)
    logger.info("auto-seeded Weaviate: %d/%d events (%d errors)", ok, len(events), bad)
    return "ok" if seeded_count(client) >= len(events) else f"{seeded_count(client)}/{len(events)} events"


def seed_file(path: Path, reset: bool = False) -> int:
    from .client import get_client
    from .corpus import load_events
    from .embed import Embedder

    events = load_events(path)
    if not events:
        raise SystemExit(f"no events in {path} (run scripts/build_events.py first)")
    client = get_client()
    ok, bad = seed(events, Embedder(), client, reset)
    print(f"inserted {ok}/{len(events)} events; collection now holds {seeded_count(client)}")
    return 0 if not bad else 1
