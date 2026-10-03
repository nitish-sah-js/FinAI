"""
scripts/seed_weaviate.py
Loads data/events.json, embeds event_text, and inserts into Weaviate HistoricalEvent collection
using deterministic UUIDs via generate_uuid5(event_id). Idempotent. Prints count.
"""
from __future__ import annotations
import json
import logging
import sys
from pathlib import Path

# Add services/vectordb and packages/copilot_common to path
_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_root / "packages" / "copilot_common"))
sys.path.insert(0, str(_root / "services" / "vectordb"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_weaviate")


def seed(events_json_path: str = "data/events.json", reset: bool = False):
    from vectordb.client import get_client
    from vectordb.schema import create_collections, HISTORICAL_EVENT_COLLECTION
    from vectordb.embed import Embedder, event_text

    try:
        from weaviate.util import generate_uuid5
    except ImportError:
        import uuid
        def generate_uuid5(val: str):
            return uuid.uuid5(uuid.NAMESPACE_DNS, str(val))

    events_path = Path(events_json_path)
    if not events_path.exists():
        raise FileNotFoundError(f"File not found: {events_json_path}")

    with open(events_path, "r", encoding="utf-8") as f:
        events = json.load(f)

    logger.info("Loaded %d events from %s", len(events), events_json_path)

    client = get_client()
    create_collections(client, reset=reset)

    coll = client.collections.get(HISTORICAL_EVENT_COLLECTION)

    embedder = Embedder()
    texts = [e.get("embedding_text") or event_text(e) for e in events]
    logger.info("Computing embeddings for %d events...", len(texts))
    vectors = embedder.encode(texts)

    from weaviate.classes.data import DataObject

    data_objects = []
    for e, vec in zip(events, vectors):
        event_id = e["event_id"]
        uid = str(generate_uuid5(event_id))

        props = {
            "event_id": event_id,
            "title": e.get("title", ""),
            "event_type": e.get("event_type", ""),
            "region": e.get("region", ""),
            "country": e.get("country", ""),
            "start_date": e.get("start_date", ""),
            "end_date": e.get("end_date", ""),
            "event_date": e.get("event_date", ""),
            "severity_value": float(e.get("severity_value", 0.0)),
            "severity_unit": e.get("severity_unit", ""),
            "severity_norm": float(e.get("severity_norm", 0.0)),
            "description": e.get("description", ""),
            "mechanism": e.get("mechanism", ""),
            "affected_assets": e.get("affected_assets", []),
            "tickers": e.get("tickers", []),
            "outcomes_json": e.get("outcomes_json") or json.dumps(e.get("outcomes", [])),
            "split": e.get("split", "train"),
            "source": e.get("source", ""),
            "source_url": e.get("source_url", ""),
            "embedding_text": e.get("embedding_text") or event_text(e),
        }

        data_objects.append(
            DataObject(
                properties=props,
                uuid=uid,
                vector=vec.tolist(),
            )
        )

    logger.info("Inserting %d events into Weaviate collection %s...", len(data_objects), HISTORICAL_EVENT_COLLECTION)
    res = coll.data.insert_many(data_objects)
    if hasattr(res, "has_errors") and res.has_errors:
        logger.warning("Some inserts had errors: %s", res.errors)
    logger.info("Inserted %d historical events successfully.", len(data_objects))
    print(f"Total inserted events: {len(data_objects)}")


if __name__ == "__main__":
    reset_coll = "--reset" in sys.argv
    seed(reset=reset_coll)
