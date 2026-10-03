import pytest

from copilot_common import reachability
from copilot_common.settings import reload_settings


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test: its own alerts.db, MOCK LLM (no Ollama), unreachable upstreams, no email/Telegram, quiet loops."""
    from monitor import config, engine as eng_mod, store
    from monitor.delivery import ws_hub
    for k, v in {"MOCK": "1", "SMTP_USER": "", "SMTP_APP_PASSWORD": "", "ALERT_EMAIL_TO": "",
                 "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "", "INGEST_URL": "http://127.0.0.1:9",
                 "AGRI_URL": "http://127.0.0.1:9", "ORCH_URL": "http://127.0.0.1:9", "L3_HOST": "10.0.0.3"}.items():
        monkeypatch.setenv(k, v)
    reload_settings()
    reachability.reset()
    cfg = config.reload_config()
    cfg["intervals_s"].update(mock_first=10 ** 6, heartbeat=10 ** 6)
    store.set_path(tmp_path / "alerts.db")
    monkeypatch.setattr(ws_hub.hub, "clients", set())
    monkeypatch.setattr(ws_hub.hub, "recent", type(ws_hub.hub.recent)(maxlen=10))
    fresh = eng_mod.Engine()
    monkeypatch.setattr(eng_mod, "engine", fresh)
    import monitor.main as main_mod
    monkeypatch.setattr(main_mod, "engine", fresh)
    yield fresh
    store.set_path(None)
    reload_settings()
