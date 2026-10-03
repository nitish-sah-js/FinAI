"""Roles → provider chains (03 §3)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .providers import Provider, build_providers
from .quota import QuotaTracker

Role = Literal["intent", "planner", "narrator", "sentiment2", "synthesizer", "red_team", "explain", "alert"]

CLOUD_SYNTH = ["groq_oss120b", "groq_qwen32b", "cerebras_oss120b", "openrouter_kimi", "ollama_L1"]


@dataclass(frozen=True)
class RoleSpec:
    local_chain: list[str]
    boost_chain: list[str]
    auto_cloud: bool
    temperature: float
    json: bool


ROLE_TABLE: dict[str, RoleSpec] = {
    "intent":      RoleSpec(["ollama_L1"], ["ollama_L1"], False, 0.0, True),
    "planner":     RoleSpec(["ollama_L1"], ["groq_qwen32b", "ollama_L1"], True, 0.0, False),
    "narrator":    RoleSpec(["ollama_L2", "ollama_L1"], ["ollama_L2", "ollama_L1"], False, 0.2, True),
    "sentiment2":  RoleSpec(["ollama_L2", "ollama_L1"], ["ollama_L2", "ollama_L1"], False, 0.0, True),
    "synthesizer": RoleSpec(["ollama_L1"], CLOUD_SYNTH, True, 0.2, False),
    "red_team":    RoleSpec(["ollama_L3_red", "ollama_L1"], ["ollama_L3_red", "ollama_L1"], False, 0.3, True),
    "explain":     RoleSpec(["ollama_L1"], ["groq_qwen32b", "ollama_L1"], False, 0.0, False),
    "alert":       RoleSpec(["ollama_L3_fast", "ollama_L1"], ["ollama_L3_fast", "ollama_L1"], False, 0.2, False),
}


def resolve_chain_with_skips(role: str, mode: str, quota: QuotaTracker, est_tokens: int,
                             providers: dict[str, Provider] | None = None) -> tuple[list[Provider], list[str]]:
    """Return (ordered providers to try, skip notes like 'openrouter_kimi:skipped(no key)')."""
    providers = providers or build_providers()
    spec = ROLE_TABLE[role]
    if mode == "boost":
        names = spec.boost_chain
    elif mode == "auto":
        first_cloud = next((n for n in spec.boost_chain if providers[n].kind == "cloud" and providers[n].available), None)
        use_cloud = spec.auto_cloud and first_cloud is not None and quota.ok(first_cloud, est_tokens)
        names = spec.boost_chain if use_cloud else spec.local_chain
    else:
        names = spec.local_chain

    chain: list[Provider] = []
    skips: list[str] = []
    for n in names:
        p = providers[n]
        if not p.available:
            skips.append(f"{n}:skipped(no key)")
            continue
        if p.kind == "cloud":
            why = quota.why_unavailable(n)
            if why:
                skips.append(f"{n}:skipped({why})")
                continue
        chain.append(p)
    if not any(p.kind == "local" for p in chain):
        chain.append(providers["ollama_L1"])     # always end on a local model
    return chain, skips


def resolve_chain(role: str, mode: str, quota: QuotaTracker, est_tokens: int) -> list[Provider]:
    return resolve_chain_with_skips(role, mode, quota, est_tokens)[0]
