"""Extract / repair JSON from small-model output and validate it against a Pydantic model (03 §8)."""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
_SINGLE_QUOTED_KEY = re.compile(r"'([A-Za-z0-9_]+)'\s*:")
_SINGLE_QUOTED_VAL = re.compile(r":\s*'([^'\\]*)'")


def _try(text: str):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def extract_json(text: str | None) -> dict | list | None:
    if not text:
        return None
    obj = _try(text)
    if obj is not None:
        return obj
    t = _THINK.sub("", text)
    m = _FENCE.search(t)
    if m:
        t = m.group(1)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        return None
    t = t[start:end + 1]
    obj = _try(t)
    if obj is not None:
        return obj
    t = _TRAILING_COMMA.sub(r"\1", t)
    t = _SINGLE_QUOTED_KEY.sub(r'"\1":', t)
    t = _SINGLE_QUOTED_VAL.sub(r': "\1"', t)
    t = re.sub(r"\bTrue\b", "true", re.sub(r"\bFalse\b", "false", re.sub(r"\bNone\b", "null", t)))
    return _try(t)


def parse_as(text: str | None, model: type[BaseModel]) -> tuple[BaseModel | None, str | None]:
    """Return (obj, None) on success or (None, error message)."""
    obj = extract_json(text)
    if obj is None:
        return None, "output is not valid JSON"
    try:
        return model.model_validate(obj), None
    except ValidationError as e:
        return None, "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()[:5])
