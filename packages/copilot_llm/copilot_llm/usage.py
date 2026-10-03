"""Per-run usage counters → FinalAnswer.llm_usage (03 §7)."""
from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class RunUsage:
    cloud_calls: int = 0
    local_calls: int = 0
    cache_hits: int = 0
    fallbacks: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    providers: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    models: dict[str, str] = field(default_factory=dict)

    @property
    def saved_calls(self) -> int:
        # every call served locally or from cache is one a cloud-only design would have paid for
        return self.local_calls + self.cache_hits

    def record(self, provider: str, kind: str, model: str, tokens_in: int, tokens_out: int, cached: bool, fallbacks: int) -> None:
        if cached:
            self.cache_hits += 1
        elif kind == "cloud":
            self.cloud_calls += 1
        else:
            self.local_calls += 1
        self.fallbacks += fallbacks
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.providers[provider] += 1
        self.models[provider] = model

    def to_dict(self, quota_snapshot: dict | None = None) -> dict:
        provs = {}
        for name, n in self.providers.items():
            q = (quota_snapshot or {}).get(name, {})
            provs[name] = {"used": n, "limit": q.get("requests_day_limit"), "model": self.models.get(name),
                           "requests_day_remaining": q.get("requests_day_remaining")}
        return {"cloud_calls": self.cloud_calls, "local_calls": self.local_calls, "cache_hits": self.cache_hits,
                "fallbacks": self.fallbacks, "saved_calls": self.saved_calls, "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out, "providers": provs}


_lock = threading.Lock()
_runs: dict[str, RunUsage] = {}
SESSION = RunUsage()


def usage_for(run_id: str | None) -> RunUsage:
    with _lock:
        key = run_id or "_no_run"
        if key not in _runs:
            _runs[key] = RunUsage()
        return _runs[key]
