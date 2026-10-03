from __future__ import annotations
import secrets
import threading
from datetime import datetime, timezone


def new_run_id() -> str:
    return f"run_{datetime.now(timezone.utc):%Y%m%d%H%M%S}_{secrets.token_hex(2)}"


def new_alert_id() -> str:
    return f"al_{datetime.now(timezone.utc):%Y%m%d}_{secrets.token_hex(3)}"


class EvidenceCounter:
    """ev_<tool>_<nnn>. The sequence is per run (shared across tools, as in the 01 examples) and thread-safe.
    Several EvidenceCounter(run_id) instances for the same run share one counter."""
    _counters: dict[str, int] = {}
    _lock = threading.Lock()
    _MAX_RUNS = 2000

    def __init__(self, run_id: str | None):
        self.run_id = run_id or "norun"

    def next(self, tool: str) -> str:
        with self._lock:
            if len(self._counters) > self._MAX_RUNS:
                for k in list(self._counters)[: self._MAX_RUNS // 2]:
                    self._counters.pop(k, None)
            n = self._counters.get(self.run_id, 0) + 1
            self._counters[self.run_id] = n
        return f"ev_{tool}_{n:03d}"
