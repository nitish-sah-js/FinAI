"""File cache with record / replay / off modes (01 §7.3)."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from .settings import get_settings


class CacheMiss(KeyError):
    pass


def cache_key(key_obj: Any) -> str:
    return hashlib.sha256(json.dumps(key_obj, sort_keys=True, default=str).encode()).hexdigest()


def _path(service: str, key: str) -> Path:
    d = get_settings().data_dir / "cache" / service
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.json"


def cache_get(service: str, key: str, ttl_s: int | None = None) -> Any:
    p = _path(service, key)
    if not p.is_file():
        raise CacheMiss(key)
    rec = json.loads(p.read_text(encoding="utf-8"))
    if ttl_s is not None and time.time() - rec.get("saved_at", 0) > ttl_s:
        raise CacheMiss(key)
    return rec["value"]


def cache_put(service: str, key: str, key_obj: Any, value: Any) -> None:
    p = _path(service, key)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"key": key_obj, "saved_at": time.time(), "value": value}, default=str), encoding="utf-8")
    tmp.replace(p)


async def cached(service: str, key_obj: dict, fn: Callable[[], Awaitable[Any]], ttl_s: int | None = None) -> Any:
    """record: return cached if fresh, else call fn and save. replay: cached or CacheMiss. off: always call."""
    mode = get_settings().CACHE_MODE
    key = cache_key(key_obj)
    if mode == "off":
        return await fn()
    if mode == "replay":
        return cache_get(service, key)
    try:
        return cache_get(service, key, ttl_s)
    except CacheMiss:
        value = await fn()
        cache_put(service, key, key_obj, value)
        return value
