"""RunState (04 §2). Plain dicts so the SQLite checkpointer can serialise everything."""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict


def _merge_dict(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


class RunState(TypedDict, total=False):
    run_id: str
    request: dict                                     # QueryRequest
    portfolio: dict                                   # Portfolio
    intent: dict                                      # Intent
    selected_agents: list[str]
    evidence: Annotated[list[dict], operator.add]     # fan-out branches append
    signals: Annotated[list[dict], operator.add]      # AgentSignal per agent
    quant: dict                                       # {"risk": ev_id, "hedge": ev_id, ...}
    draft: str                                        # synthesizer markdown
    answer_json: dict                                 # synthesizer <json> tail
    red_team: dict
    validator: dict
    answer_markdown: str
    explanation: dict
    steps: int
    latency: Annotated[dict, _merge_dict]             # per-stage ms
    errors: Annotated[list[str], operator.add]
