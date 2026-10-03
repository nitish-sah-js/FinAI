from __future__ import annotations
import hashlib
import inspect
import json
import time
from pathlib import Path
from typing import Any
from .settings import settings, get_data_dir


class CacheMiss(Exception):
    pass


def _key(key_obj: dict) -> str:
    return hashlib.sha256(json.dumps(key_obj, sort_keys=True, default=str).encode()).hexdigest()


def _path(service: str, key_obj: dict) -> Path:
    return get_data_dir() / "cache" / service / f"{_key(key_obj)}.json"


def _load(p: Path, ttl_s: int | None):
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text())
    except Exception:
        return None
    if ttl_s is not None and time.time() - blob.get("saved_ts", 0) > ttl_s:
        return None
    return blob


async def cached(service: str, key_obj: dict, fn, ttl_s: int | None = None) -> Any:
    """record: call fn and save. replay: return saved or raise CacheMiss. off: always call fn.
    fn may be sync or async and must return JSON-serialisable data."""
    mode = (settings.CACHE_MODE or "record").lower()
    p = _path(service, key_obj)
    if mode == "replay":
        blob = _load(p, None)
        if blob is None:
            raise CacheMiss(f"{service}:{_key(key_obj)[:12]}")
        return blob["value"]
    res = fn()
    if inspect.isawaitable(res):
        res = await res
    if mode == "record":
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"key": key_obj, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                     "saved_ts": time.time(), "value": res}, default=str))
        except Exception:
            pass
    return res
