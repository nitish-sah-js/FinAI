"""Load and render prompt templates (05 §5 step 2)."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")       # only {snake_case}; JSON braces in the prompts are left alone
WITH_GROUND_RULES = {"P4", "P5", "P6", "P7", "P8", "P8_hi", "P9", "P10"}


@lru_cache
def load(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8").strip()


def _fmt(v) -> str:
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(",", ":"), ensure_ascii=False, default=str)
    return str(v)


def render(name: str, **vars) -> str:
    text = load(name)
    if name in WITH_GROUND_RULES:
        text = load("ground_rules") + "\n" + text

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in vars:
            raise KeyError(f"prompt {name}: missing placeholder {{{key}}}")
        return _fmt(vars[key])

    return _PLACEHOLDER.sub(sub, text)
