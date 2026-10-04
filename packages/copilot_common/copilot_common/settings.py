"""Settings read from .env with ${VAR} expansion (01 §3)."""
from __future__ import annotations

import os
import sys
import re
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict

_VAR = re.compile(r"\$\{([A-Z0-9_]+)\}")


def project_root() -> Path:
    """Repo root = first parent containing a docs/ or packages/ folder; override with COPILOT_ROOT."""
    if os.environ.get("COPILOT_ROOT"):
        return Path(os.environ["COPILOT_ROOT"])
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "packages").is_dir() and (p / "services").is_dir():
            return p
    return Path.cwd()


def _load_env_file() -> None:
    """Load .env (cwd first, then repo root) into os.environ, expanding ${VAR}. Real env vars win.
    Under pytest the laptop's .env is NOT read: tests must not depend on which laptop runs them (a cluster .env
    adds CLUSTER_KEY and LAN IPs) and must never see real secrets such as SMTP credentials.
    COPILOT_TEST_DOTENV=1 opts back in."""
    skip_file = "pytest" in sys.modules and os.environ.get("COPILOT_TEST_DOTENV") != "1"
    for path in () if skip_file else (Path.cwd() / ".env", project_root() / ".env"):
        if path.is_file():
            raw = dotenv_values(path)
            for k, v in raw.items():
                if v is not None and k not in os.environ:
                    os.environ[k] = v
            break
    # expand ${VAR} references (a few passes handle chains)
    for _ in range(3):
        for k, v in list(os.environ.items()):
            if "${" in v:
                os.environ[k] = _VAR.sub(lambda m: os.environ.get(m.group(1), ""), v)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    L1_HOST: str = "127.0.0.1"
    L2_HOST: str = "127.0.0.1"
    L3_HOST: str = "127.0.0.1"

    ORCH_URL: str = "http://127.0.0.1:8000"
    QUANT_URL: str = "http://127.0.0.1:8101"
    SENTIMENT_URL: str = "http://127.0.0.1:8102"
    AGRI_URL: str = "http://127.0.0.1:8103"
    VECTOR_URL: str = "http://127.0.0.1:8104"
    WEAVIATE_HOST: str = "127.0.0.1"
    INGEST_URL: str = "http://127.0.0.1:8201"
    MONITOR_URL: str = "http://127.0.0.1:8202"

    OLLAMA_L1: str = "http://127.0.0.1:11434"
    OLLAMA_L2: str = "http://127.0.0.1:11434"
    OLLAMA_L3: str = "http://127.0.0.1:11434"

    # Ollama model tags per role. NOTE: the bare "qwen3:4b" tag is the thinking-only 2507 build (it cannot turn
    # thinking off and writes its reasoning into the answer), so L1 defaults to the non-thinking instruct build.
    OLLAMA_MODEL_L1: str = "qwen3:4b-instruct"
    OLLAMA_MODEL_L2: str = "gemma3:4b"
    OLLAMA_MODEL_L3_FAST: str = "qwen3:1.7b"
    OLLAMA_MODEL_L3_RED: str = "phi4-mini"

    MOCK: bool = False
    CLUSTER_KEY: str = ""                # shared secret for X-Cluster-Key between laptops; empty = auth off
    DEMO_MODE: bool = False              # show synthetic demo data (e.g. the DEMO-ODISHA storm), stamped SIMULATED
    CACHE_MODE: str = "record"           # record | replay | off
    LLM_MODE: str = "local"              # local | boost | auto
    GROQ_API_KEY: str = ""
    CEREBRAS_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    SMTP_USER: str = ""                  # monitor Tier-3 email (11 §7); app password, never committed
    SMTP_APP_PASSWORD: str = ""
    ALERT_EMAIL_TO: str = ""
    FRED_API_KEY: str = ""
    EIA_API_KEY: str = ""

    AUTO_MIN_RPD: int = 50
    LLM_REPLAY_STRICT: bool = False
    MOCK_DELAY_MS: int = 300
    EMBED_DEVICE: str = "cpu"

    DATA_DIR: str = ""                   # default: <root>/data

    @property
    def data_dir(self) -> Path:
        d = Path(self.DATA_DIR) if self.DATA_DIR else project_root() / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d


@lru_cache
def get_settings() -> Settings:
    _load_env_file()
    return Settings()


def reload_settings() -> Settings:
    """For tests: re-read env vars."""
    get_settings.cache_clear()
    return get_settings()


class _LiveSettings:
    """Module-level `settings` that always reads (and writes) the CURRENT Settings object, so it stays correct after
    reload_settings() and `monkeypatch.setattr(settings, "MOCK", 1)` works. Kept for code written against the
    early stand-in package (services/quant); new code should call get_settings()."""

    def __getattr__(self, name):
        return getattr(get_settings(), name)

    def __setattr__(self, name, value):
        setattr(get_settings(), name, value)


settings = _LiveSettings()


def get_data_dir() -> Path:
    """The shared data/ folder (DATA_DIR override, else <repo>/data)."""
    return get_settings().data_dir
