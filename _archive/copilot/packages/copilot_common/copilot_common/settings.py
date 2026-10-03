from __future__ import annotations
import os
import re
from pathlib import Path
from dotenv import dotenv_values, find_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    MOCK: int = 0
    CACHE_MODE: str = "record"      # record | replay | off
    LLM_MODE: str = "local"
    GROQ_API_KEY: str = ""
    CEREBRAS_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    FRED_API_KEY: str = ""
    EIA_API_KEY: str = ""
    AUTO_MIN_RPD: int = 50
    LLM_REPLAY_STRICT: int = 0
    MOCK_DELAY_MS: int = 300
    EMBED_DEVICE: str = "cpu"
    DATA_DIR: str = ""              # optional override for the shared data/ folder


_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand(values: dict[str, str]) -> dict[str, str]:
    out = dict(values)
    for _ in range(5):  # resolve nested ${VAR}
        changed = False
        for k, v in out.items():
            if isinstance(v, str) and "${" in v:
                nv = _VAR.sub(lambda m: out.get(m.group(1), os.environ.get(m.group(1), "")) or "", v)
                if nv != v:
                    out[k] = nv
                    changed = True
        if not changed:
            break
    return out


def _load() -> Settings:
    merged = {k: v for k, v in dotenv_values(find_dotenv(usecwd=True)).items() if v is not None}
    merged.update(os.environ)
    merged = _expand(merged)
    fields = set(Settings.model_fields)
    return Settings(**{k: v for k, v in merged.items() if k in fields})


settings = _load()


def get_data_dir() -> Path:
    """DATA_DIR env > nearest ./data found walking up from cwd > cwd/data."""
    if settings.DATA_DIR:
        return Path(settings.DATA_DIR)
    here = Path.cwd().resolve()
    for p in [here, *here.parents]:
        if (p / "data").is_dir():
            return p / "data"
    return here / "data"
