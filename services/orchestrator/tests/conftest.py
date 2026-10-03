import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))      # services/ → import orchestrator

from copilot_common.settings import reload_settings  # noqa: E402


@pytest.fixture(autouse=True)
def mock_env(tmp_path, monkeypatch):
    """Every test: MOCK=1 (fixtures, canned LLM), isolated data dir, fresh ledger + graph."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MOCK", "1")
    monkeypatch.setenv("MOCK_DELAY_MS", "20")
    monkeypatch.setenv("LLM_MODE", "local")
    monkeypatch.setenv("CACHE_MODE", "off")
    reload_settings()
    from copilot_common import reachability
    reachability.reset()
    from orchestrator import graph as G
    from orchestrator.ledger import ledger
    from copilot_llm import llm
    ledger._db = None
    G._graph = None
    llm.reset()
    yield
    ledger._db = None
    G._graph = None
    reload_settings()
