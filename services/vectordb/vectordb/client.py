"""Weaviate v4 client singleton with a circuit breaker (copilot_common.reachability).

Request path never pays a reconnect: if the breaker says Weaviate is down, callers go straight to the numpy fallback.
A fast TCP probe (≤ CONNECT_TIMEOUT_S) runs before the real connect so a closed port costs at most one probe per
DOWN_TTL_S, and main.py's background prober keeps the state fresh so requests do not probe at all.

NOTE: everything that talks to a live Weaviate here is written against weaviate-client 4.23 but was NOT exercised
against a running server (Docker was down while this was integrated).
"""
from __future__ import annotations

import logging
import socket
import threading
from typing import Any

from copilot_common import reachability
from copilot_common.settings import get_settings

try:
    import weaviate
except ImportError:  # pragma: no cover
    weaviate = None

logger = logging.getLogger(__name__)

HTTP_PORT = 8080
GRPC_PORT = 50051
PROBE_TIMEOUT_S = min(1.0, reachability.CONNECT_TIMEOUT_S)

_client: Any = None
_lock = threading.Lock()
_forced_down = False          # tests / chaos: pretend Weaviate is down without touching the network


def weaviate_url() -> str:
    return f"http://{get_settings().WEAVIATE_HOST}:{HTTP_PORT}"


def force_down(flag: bool = True) -> None:
    global _forced_down
    _forced_down = flag
    if flag:
        close_client()


def _tcp_probe(host: str, port: int, timeout: float = PROBE_TIMEOUT_S) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_available() -> bool:
    """Cheap check used on the request path. Never connects when the breaker is open."""
    if _forced_down or weaviate is None:
        return False
    if reachability.is_down(weaviate_url()):
        return False
    c = _client
    if c is not None:
        try:
            return bool(c.is_connected())
        except Exception:  # noqa: BLE001
            return False
    return True   # unknown: get_client() will try (once per breaker window)


def get_client() -> Any:
    """Connected client or raise. Marks the breaker down on any connect failure."""
    global _client
    if weaviate is None:
        raise RuntimeError("weaviate-client is not installed")
    if _forced_down:
        raise ConnectionError("weaviate forced down")
    url = weaviate_url()
    if reachability.is_down(url):
        raise ConnectionError(f"{reachability.host_key(url)} marked down")
    c = _client
    if c is not None:
        try:
            if c.is_connected():
                return c
        except Exception:  # noqa: BLE001
            pass
    with _lock:
        if _client is not None:
            try:
                if _client.is_connected():
                    return _client
            except Exception:  # noqa: BLE001
                pass
            close_client()
        host = get_settings().WEAVIATE_HOST
        if not _tcp_probe(host, HTTP_PORT):
            reachability.mark_down(url)
            raise ConnectionError(f"weaviate {host}:{HTTP_PORT} not reachable")
        try:
            from weaviate.classes.init import AdditionalConfig, Timeout
            _client = weaviate.connect_to_local(
                host=host, port=HTTP_PORT, grpc_port=GRPC_PORT,
                additional_config=AdditionalConfig(timeout=Timeout(init=reachability.CONNECT_TIMEOUT_S * 2,
                                                                   query=10, insert=60)))
            reachability.mark_up(url)
            logger.info("Connected to Weaviate at %s", url)
            return _client
        except Exception as e:  # noqa: BLE001
            _client = None
            reachability.mark_down(url)
            raise ConnectionError(f"weaviate connect failed: {type(e).__name__}: {e}") from e


def report_failure(exc: BaseException) -> None:
    """A query failed on a live client: drop it and open the breaker so the next requests use the fallback."""
    logger.warning("Weaviate call failed (%s); opening the breaker", exc)
    close_client()
    reachability.mark_down(weaviate_url())


def close_client() -> None:
    global _client
    c, _client = _client, None
    if c is not None:
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass


def probe() -> str:
    """Blocking health probe used by deps_check / the background prober: 'ok' or 'down'. Respects the breaker."""
    if _forced_down or weaviate is None:
        return "down"
    try:
        c = get_client()
        c.collections.list_all(simple=True)
        return "ok"
    except Exception:  # noqa: BLE001
        return "down"


def refresh() -> str:
    """Background prober: ignore the breaker window and re-probe (TCP first, so a dead host costs ≤ 1 s)."""
    if _forced_down or weaviate is None:
        return "down"
    url = weaviate_url()
    if reachability.is_down(url):
        if not _tcp_probe(get_settings().WEAVIATE_HOST, HTTP_PORT):
            reachability.mark_down(url)          # keep it open
            return "down"
        reachability.mark_up(url)
    return probe()


async def weaviate_ready() -> str:
    """Async health helper (does not block the loop): 'ok' | 'down'."""
    if not is_available():
        return "down"
    import asyncio
    return await asyncio.to_thread(probe)
