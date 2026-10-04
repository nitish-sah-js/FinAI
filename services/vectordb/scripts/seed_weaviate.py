"""data/historical_events.json → embed (bge-small) → Weaviate HistoricalEvent (08 §8 step 6). Idempotent:
uuid = generate_uuid5(event_id), so re-running replaces objects instead of duplicating them.

    docker compose -f infra/docker-compose.yml up -d weaviate
    cd services/vectordb && ../../.venv/Scripts/python scripts/seed_weaviate.py [--reset]

The running vectordb service also seeds an empty Weaviate by itself (vectordb/seed.py ensure_seeded).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vectordb.seed import seed_file, to_properties  # noqa: E402,F401  (to_properties kept for old imports)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    from vectordb.paths import corpus_path
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=Path, default=None)
    ap.add_argument("--reset", action="store_true", help="drop and recreate HistoricalEvent first")
    a = ap.parse_args(argv)
    return seed_file(a.events or corpus_path(), a.reset)


if __name__ == "__main__":
    raise SystemExit(main())
