"""Circuit breaker for LAN hosts: after one failed connection, skip that host for a while instead of paying the
connect timeout again (Windows takes ~2 s to refuse a connection to a closed port, even on 127.0.0.1).

Keyed by scheme://host:port, shared by every service and the LLM gateway in one process.
"""
from __future__ import annotations

import os
import threading
import time
from urllib.parse import urlsplit

DOWN_TTL_S = 30.0          # retry a down host after this long (so a laptop that comes back is picked up quickly)
# A healthy wired LAN host connects in milliseconds, but a phone hotspot has latency spikes: at 1.5 s one slow
# connect wrote off L2's quant for 30 s in the first 3-laptop run. Override with CONNECT_TIMEOUT_S in the env.
CONNECT_TIMEOUT_S = float(os.environ.get("CONNECT_TIMEOUT_S") or 3.0)

_lock = threading.Lock()
_down_until: dict[str, float] = {}


def host_key(url: str) -> str:
    u = urlsplit(url)
    port = u.port or (443 if u.scheme == "https" else 80)
    return f"{u.scheme}://{u.hostname}:{port}"


def is_down(url: str) -> bool:
    with _lock:
        t = _down_until.get(host_key(url))
        return t is not None and time.time() < t


def mark_down(url: str, ttl_s: float = DOWN_TTL_S) -> None:
    with _lock:
        _down_until[host_key(url)] = time.time() + ttl_s


def mark_up(url: str) -> None:
    with _lock:
        _down_until.pop(host_key(url), None)


def down_hosts() -> dict[str, int]:
    now = time.time()
    with _lock:
        return {k: int(v - now) for k, v in _down_until.items() if v > now}


def reset() -> None:
    with _lock:
        _down_until.clear()
