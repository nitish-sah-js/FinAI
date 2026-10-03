"""Provider registry (03 §2). Built from Settings so hosts/keys come from .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from copilot_common.settings import get_settings


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    model: str
    api_key_env: str | None          # None → local Ollama (dummy key)
    kind: Literal["cloud", "local"]
    max_ctx: int

    @property
    def api_key(self) -> str:
        if self.api_key_env is None:
            return "ollama"
        return os.environ.get(self.api_key_env) or getattr(get_settings(), self.api_key_env, "") or ""

    @property
    def available(self) -> bool:
        """A cloud provider without a key is skipped silently."""
        return self.kind == "local" or bool(self.api_key)

    @property
    def host_label(self) -> str:
        """Short label used in AgentEvent.provider, e.g. 'ollama@L1' or 'groq'."""
        if self.kind == "local":
            return "ollama@" + self.name.split("_")[1]
        return self.name.split("_")[0]


GROQ = "https://api.groq.com/openai/v1"


def build_providers() -> dict[str, Provider]:
    s = get_settings()
    v1 = lambda base: base.rstrip("/") + "/v1"  # noqa: E731
    return {p.name: p for p in [
        Provider("groq_oss120b", GROQ, "openai/gpt-oss-120b", "GROQ_API_KEY", "cloud", 131_072),
        Provider("groq_qwen32b", GROQ, "qwen/qwen3-32b", "GROQ_API_KEY", "cloud", 131_072),
        Provider("cerebras_oss120b", "https://api.cerebras.ai/v1", "gpt-oss-120b", "CEREBRAS_API_KEY", "cloud", 8_192),
        Provider("openrouter_kimi", "https://openrouter.ai/api/v1", "moonshotai/kimi-k2.6:free", "OPENROUTER_API_KEY", "cloud", 131_072),
        Provider("ollama_L1", v1(s.OLLAMA_L1), s.OLLAMA_MODEL_L1, None, "local", 8_192),
        Provider("ollama_L2", v1(s.OLLAMA_L2), s.OLLAMA_MODEL_L2, None, "local", 8_192),
        Provider("ollama_L3_fast", v1(s.OLLAMA_L3), s.OLLAMA_MODEL_L3_FAST, None, "local", 8_192),
        Provider("ollama_L3_red", v1(s.OLLAMA_L3), s.OLLAMA_MODEL_L3_RED, None, "local", 8_192),
    ]}
