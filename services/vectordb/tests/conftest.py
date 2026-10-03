"""Test setup: MOCK off, Weaviate treated as DOWN via the real circuit breaker (no network), bge model warmed once."""
from __future__ import annotations

import os

import pytest

os.environ.pop("MOCK", None)
os.environ.setdefault("EMBED_DEVICE", "cpu")

from copilot_common import reachability            # noqa: E402
from copilot_common.settings import reload_settings  # noqa: E402

reload_settings()

from vectordb import client as wv                   # noqa: E402
from vectordb.embed import Embedder                 # noqa: E402


@pytest.fixture(autouse=True)
def weaviate_breaker_open():
    """Every test runs with the breaker open for Weaviate (Docker is not running here anyway)."""
    reachability.mark_down(wv.weaviate_url(), ttl_s=3600)
    yield
    reachability.mark_down(wv.weaviate_url(), ttl_s=3600)


@pytest.fixture(scope="session")
def embedder() -> Embedder:
    e = Embedder()
    assert not e.is_fallback, "BAAI/bge-small-en-v1.5 must load for these tests"
    return e
