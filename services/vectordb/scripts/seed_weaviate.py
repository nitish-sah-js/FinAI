"""data/historical_events.json → embed (bge-small) → Weaviate HistoricalEvent (08 §8 step 6). Idempotent:
uuid = generate_uuid5(event_id), so re-running replaces objects instead of duplicating them.

    docker compose -f infra/docker-compose.yml up -d weaviate
    cd services/vectordb && ../../.venv/Scripts/python scripts/seed_weaviate.py [--reset]

UNTESTED against a live Weaviate (Docker was down during integration).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logger = logging.getLogger("seed_weaviate")


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


def seed(path: Path, reset: bool = False) -> int:
    from weaviate.classes.data import DataObject
    from weaviate.util import generate_uuid5

    from vectordb.client import get_client
    from vectordb.corpus import load_events
    from vectordb.embed import Embedder, event_text
    from vectordb.schema import HISTORICAL_EVENT_COLLECTION, create_collections

    events = load_events(path)
    if not events:
        raise SystemExit(f"no events in {path} (run scripts/build_events.py first)")
    emb = Embedder()
    if emb.is_fallback:
        raise SystemExit("bge-small-en-v1.5 not available: refusing to seed hashing vectors")
    texts = [e.get("embedding_text") or event_text(e) for e in events]
    vecs = emb.encode(texts)
    client = get_client()
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
    n_ok = len(objs) - len(errors)
    total = coll.aggregate.over_all(total_count=True).total_count
    print(f"inserted {n_ok}/{len(objs)} events; collection {HISTORICAL_EVENT_COLLECTION} now holds {total}")
    client.close()
    return 0 if not errors else 1


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    from vectordb.paths import corpus_path
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=None)
    ap.add_argument("--reset", action="store_true", help="drop and recreate HistoricalEvent first")
    a = ap.parse_args(argv)
    return seed(a.events or corpus_path(), a.reset)


if __name__ == "__main__":
    raise SystemExit(main())
