"""
vectordb/client.py
Singleton weaviate v4 client. Connects to Weaviate running on settings.WEAVIATE_HOST.
Handles offline/missing client gracefully.
"""
from __future__ import annotations
import logging
import threading
from typing import Any, Optional

try:
    import weaviate
except ImportError:
    weaviate = None

from copilot_common.settings import settings

logger = logging.getLogger(__name__)

_client: Any = None
_lock = threading.Lock()


def get_client() -> Any:
    """Return the shared Weaviate client, creating it on first call."""
    global _client
    if weaviate is None:
        raise RuntimeError("weaviate-client is not installed")

    if _client is not None and getattr(_client, "is_connected", lambda: False)():
        return _client
    with _lock:
        if _client is not None and getattr(_client, "is_connected", lambda: False)():
            return _client
        logger.info("Connecting to Weaviate at %s:8080", settings.weaviate_host)
        _client = weaviate.connect_to_local(
            host=settings.weaviate_host,
            port=8080,
            grpc_port=50051,
        )
        return _client


def close_client() -> None:
    """Close the shared client (call on app shutdown)."""
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass
        _client = None


def weaviate_ready() -> str:
    """Health-check helper: returns 'ok' or raises."""
    if weaviate is None:
        raise RuntimeError("weaviate-client not installed")
    client = get_client()
    if not client.is_connected():
        raise RuntimeError("Weaviate client not connected")
    client.collections.list_all()
    return "ok"
