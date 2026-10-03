"""The analog corpus (data/historical_events.json) with cached vectors, and the backtest holdout id set.

Vectors are computed once per (file mtime, embedder) and reused by every request: the numpy fallback costs one query
embedding plus a 40×384 matrix product.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import numpy as np

from .embed import Embedder, event_text
from .paths import corpus_path, holdout_path

logger = logging.getLogger(__name__)

# 08 §4.2. Kept in code as a safety net; the backtest file data/events.json is unioned in at runtime.
HOLDOUT_IDS = frozenset({
    "hurricane_ida_2021", "cyclone_biparjoy_2023", "cyclone_michaung_2023", "monsoon_deficit_2015",
    "heatwave_2022", "rbi_offcycle_hike_2022", "abqaiq_attack_2019", "rice_export_ban_2023",
})

_lock = threading.Lock()
_events_cache: dict = {"path": None, "mtime": None, "events": []}
_vec_cache: dict = {"key": None, "vecs": None}
_holdout_cache: dict = {"path": None, "mtime": None, "ids": frozenset()}


def _mtime(p: Path) -> float | None:
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def load_events(path: Path | None = None) -> list[dict]:
    p = Path(path) if path else corpus_path()
    m = _mtime(p)
    if m is None:
        logger.warning("analog corpus %s not found (run scripts/build_events.py)", p)
        return []
    with _lock:
        if _events_cache["path"] != str(p) or _events_cache["mtime"] != m:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("events", [])
            _events_cache.update(path=str(p), mtime=m, events=data)
        return _events_cache["events"]


def corpus_vectors(events: list[dict], embedder: Embedder) -> np.ndarray:
    """(n, 384) normalised vectors of the corpus embedding texts; cached until the file or embedder changes."""
    key = (_events_cache.get("path"), _events_cache.get("mtime"), len(events), id(events), embedder.name)
    with _lock:
        if _vec_cache["key"] == key and _vec_cache["vecs"] is not None:
            return _vec_cache["vecs"]
    texts = [e.get("embedding_text") or event_text(e) for e in events]
    vecs = embedder.encode(texts)
    with _lock:
        _vec_cache.update(key=key, vecs=vecs)
    return vecs


def holdout_ids(path: Path | None = None) -> frozenset[str]:
    """Every holdout id: the 8 from 08 §4.2 ∪ split=="holdout" records of the backtest file data/events.json."""
    p = Path(path) if path else holdout_path()
    m = _mtime(p)
    if m is None:
        return HOLDOUT_IDS
    if _holdout_cache["path"] != str(p) or _holdout_cache["mtime"] != m:
        try:
            recs = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(recs, dict):
                recs = recs.get("events", [])
            ids = {r["event_id"] for r in recs if isinstance(r, dict) and r.get("event_id")
                   and r.get("split", "holdout") != "train"}
        except (OSError, ValueError, KeyError) as e:
            logger.warning("cannot read holdout file %s: %s", p, e)
            ids = set()
        _holdout_cache.update(path=str(p), mtime=m, ids=frozenset(ids))
    return HOLDOUT_IDS | _holdout_cache["ids"]


def clear_caches() -> None:
    with _lock:
        _events_cache.update(path=None, mtime=None, events=[])
        _vec_cache.update(key=None, vecs=None)
    _holdout_cache.update(path=None, mtime=None, ids=frozenset())
