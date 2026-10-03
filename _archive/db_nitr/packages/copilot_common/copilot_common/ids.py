from __future__ import annotations
import secrets
import threading
import time
from datetime import datetime, timezone


def new_run_id() -> str:
    """run_<yyyymmddHHMMSS>_<4hex>"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    hex4 = secrets.token_hex(2)
    return f"run_{ts}_{hex4}"


def new_alert_id() -> str:
    """al_<yyyymmdd>_<6hex>"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    hex6 = secrets.token_hex(3)
    return f"al_{ts}_{hex6}"


class EvidenceCounter:
    """Thread-safe per-run evidence ID counter."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}

    def next(self, tool: str) -> str:
        """ev_<tool>_<nnn> — three-digit, 1-indexed, thread-safe."""
        with self._lock:
            self._counters[tool] = self._counters.get(tool, 0) + 1
            n = self._counters[tool]
        return f"ev_{tool}_{n:03d}"
