"""config.yaml loader (path is relative to this package, not the working directory)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).parent / "config.yaml"


@lru_cache
def get_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def reload_config() -> dict:
    get_config.cache_clear()
    return get_config()
