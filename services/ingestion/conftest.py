import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest


@pytest.fixture
def env(monkeypatch):
    """env(DATA_DIR=..., MOCK="0") sets env vars AND reloads copilot_common settings (get_settings is cached)."""
    from copilot_common.settings import reload_settings

    def _set(**kv):
        for k, v in kv.items():
            monkeypatch.setenv(k, str(v))
        reload_settings()
    yield _set
    monkeypatch.undo()
    reload_settings()
