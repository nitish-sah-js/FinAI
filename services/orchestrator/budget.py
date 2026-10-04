"""Per-node time/token budgets (04 §4)."""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

AGENT_BUDGET = {   # node: (timeout_s, max_tokens for the LLM part)
    "parse_intent": (8, 300), "sentiment_agent": (15, 0), "weather_agent": (12, 300),
    "agri_agent": (12, 300), "macro_agent": (12, 300), "analog_agent": (12, 400),
    "exposure_agent": (8, 0), "quant_agent": (20, 0), "planner": (10, 300),
    "synthesizer": (40, 1400), "red_team": (20, 400), "validator": (3, 0), "explain": (30, 700)}
RUN_DEADLINE_S = 90
MAX_STEPS = 6          # planner loop cap


def timeout_for(node: str) -> float:
    return AGENT_BUDGET.get(node, (15, 0))[0]


def tokens_for(node: str) -> int | None:
    return AGENT_BUDGET.get(node, (15, 0))[1] or None


class Budget:
    def __init__(self, node: str):
        self.node = node
        self.t0 = time.perf_counter()
        self.tokens_in = 0
        self.tokens_out = 0
        self.model: str | None = None
        self.provider: str | None = None
        self.evidence: list = []        # evidence fetched so far: kept if the node runs out of time afterwards

    @property
    def ms(self) -> int:
        return int((time.perf_counter() - self.t0) * 1000)

    @property
    def max_tokens(self) -> int | None:
        return tokens_for(self.node)

    def add_llm(self, info: dict) -> None:
        self.tokens_in += info.get("tokens_in") or 0
        self.tokens_out += info.get("tokens_out") or 0
        self.model = info.get("model") or self.model
        self.provider = info.get("provider") or self.provider


@asynccontextmanager
async def budget(node: str, timeout_s: float | None = None):
    """async with budget("weather_agent") as b: ... → raises TimeoutError when the node's time budget is spent."""
    b = Budget(node)
    async with asyncio.timeout(timeout_s if timeout_s is not None else timeout_for(node)):
        yield b
