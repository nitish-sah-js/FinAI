from __future__ import annotations
import os
import re
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator


def _expand_env(v: str) -> str:
    """Expand ${VAR} references using os.environ."""
    return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), v)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_file_encoding="utf-8")

    # Hosts
    l1_host: str = "127.0.0.1"
    l2_host: str = "127.0.0.1"
    l3_host: str = "127.0.0.1"

    # Service URLs
    orch_url: str = "http://127.0.0.1:8000"
    quant_url: str = "http://127.0.0.1:8101"
    sentiment_url: str = "http://127.0.0.1:8102"
    agri_url: str = "http://127.0.0.1:8103"
    vector_url: str = "http://127.0.0.1:8104"
    weaviate_host: str = "127.0.0.1"
    ingest_url: str = "http://127.0.0.1:8201"
    monitor_url: str = "http://127.0.0.1:8202"

    # Ollama
    ollama_l1: str = "http://127.0.0.1:11434"
    ollama_l2: str = "http://127.0.0.1:11434"
    ollama_l3: str = "http://127.0.0.1:11434"

    # Mode flags
    mock: bool = False
    cache_mode: str = "off"          # record | replay | off
    llm_mode: str = "local"          # local | boost | auto
    mock_delay_ms: int = 300
    embed_device: str = "cpu"        # cpu | cuda

    # API Keys
    groq_api_key: str = ""
    cerebras_api_key: str = ""
    openrouter_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    fred_api_key: str = ""
    eia_api_key: str = ""

    # Auto-mode thresholds
    auto_min_rpd: int = 50
    llm_replay_strict: bool = False

    @model_validator(mode="before")
    @classmethod
    def expand_vars(cls, data: dict) -> dict:
        return {k: _expand_env(v) if isinstance(v, str) else v for k, v in data.items()}


settings = Settings()
