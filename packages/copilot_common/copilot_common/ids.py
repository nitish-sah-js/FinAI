"""ID helpers (01 §4)."""
from __future__ import annotations

import secrets
import threading
from collections import defaultdict
from datetime import datetime, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_run_id() -> str:
    return f"run_{_now():%Y%m%d%H%M%S}_{secrets.token_hex(2)}"


def new_alert_id() -> str:
    return f"al_{_now():%Y%m%d}_{secrets.token_hex(3)}"


class EvidenceCounter:
    """Per-run, thread-safe evidence id generator: ev_<tool>_<nnn>."""

    def __init__(self, run_id: str | None = None):
        self.run_id = run_id
        self._lock = threading.Lock()
        self._counts: dict[str, int] = defaultdict(int)

    def next(self, tool: str) -> str:
        with self._lock:
            self._counts[tool] += 1
            return f"ev_{tool}_{self._counts[tool]:03d}"


_counters: dict[str, EvidenceCounter] = {}
_counters_lock = threading.Lock()


def counter_for(run_id: str) -> EvidenceCounter:
    with _counters_lock:
        if run_id not in _counters:
            _counters[run_id] = EvidenceCounter(run_id)
        return _counters[run_id]
