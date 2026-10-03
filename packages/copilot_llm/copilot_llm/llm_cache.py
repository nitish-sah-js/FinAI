"""LLM response cache keyed by hash(model, messages, tools, temperature, response_format) (03 §5)."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from copilot_common.settings import get_settings


def key(model: str, messages: list[dict], tools: list[dict] | None, temperature: float, response_format) -> str:
    blob = json.dumps({"model": model, "messages": messages, "tools": tools, "temperature": temperature,
                       "response_format": response_format}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _dir() -> Path:
    d = get_settings().data_dir / "cache" / "llm"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get(k: str) -> dict | None:
    """Return the cached record or None. Ignores CACHE_MODE=off."""
    if get_settings().CACHE_MODE == "off":
        return None
    p = _dir() / f"{k}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def put(k: str, record: dict) -> None:
    if get_settings().CACHE_MODE == "off":
        return
    record = {**record, "saved_at": time.time()}
    p = _dir() / f"{k}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, default=str), encoding="utf-8")
    tmp.replace(p)
