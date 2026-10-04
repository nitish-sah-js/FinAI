"""copilot_llm: the only way any module talks to an LLM (docs/03_LLM_GATEWAY.md).

    from copilot_llm import llm
    res = await llm.chat("intent", messages, schema=Intent, run_id=run_id)
"""
from . import calls
from .calls import NO_CACHE
from .chaos import FORCE_RATE_LIMIT
from .gateway import Gateway, LLMResult, llm
from .routing import ROLE_TABLE, Role
from .usage import SESSION, usage_for

__all__ = ["llm", "Gateway", "LLMResult", "Role", "ROLE_TABLE", "FORCE_RATE_LIMIT", "usage_for", "SESSION", "calls", "NO_CACHE"]
