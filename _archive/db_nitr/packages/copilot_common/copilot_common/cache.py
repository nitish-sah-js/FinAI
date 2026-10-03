from __future__ import annotations
import json
import hashlib
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable

from .settings import settings


class CacheMiss(Exception):
    pass


def _cache_path(service: str, key_obj: dict) -> Path:
    key_str = json.dumps(key_obj, sort_keys=True, default=str)
    sha = hashlib.sha256(key_str.encode()).hexdigest()
    p = Path("data") / "cache" / service / f"{sha}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


async def cached(
    service: str,
    key_obj: dict,
    fn: Callable[[], Awaitable[Any]],
    ttl_s: int | None = None,
) -> Any:
    """
    CACHE_MODE=record: call fn, save result.
    CACHE_MODE=replay: return saved result (raise CacheMiss if absent).
    CACHE_MODE=off: always call fn.
    """
    mode = settings.cache_mode
    path = _cache_path(service, key_obj)

    if mode == "replay":
        if not path.exists():
            raise CacheMiss(f"No cache entry for {service}/{path.name}")
        data = json.loads(path.read_text())
        return data["value"]

    result = await fn()

    if mode == "record":
        payload = {
            "key": key_obj,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "value": result,
        }
        path.write_text(json.dumps(payload, default=str, indent=2))

    return result
