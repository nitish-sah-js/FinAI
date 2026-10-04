"""Shared-secret auth between the cluster's services (X-Cluster-Key).

Server side: ClusterKeyMiddleware (added by service_base.create_service_app) rejects HTTP requests without the right
X-Cluster-Key header (401) and WebSocket connections without it (close 4401). Browsers cannot set headers on a
WebSocket, so `?key=` in the query string is accepted too. /health stays open so liveness checks work without a key.
Client side: install_httpx_hook() makes every httpx client add the header, but ONLY on requests to our own services
(the *_URL settings), so the key never goes to Ollama or outside APIs (Open-Meteo, GDELT, Yahoo, FRED ...).
With CLUSTER_KEY empty (single-laptop default) auth is off and nothing changes.
"""
from __future__ import annotations

import hmac
from urllib.parse import parse_qs, urlsplit

import httpx

from .settings import get_settings

HEADER = "X-Cluster-Key"
OPEN_PATHS = ("/health", "/docs", "/openapi.json", "/redoc")
PEER_URL_SETTINGS = ("ORCH_URL", "QUANT_URL", "SENTIMENT_URL", "AGRI_URL", "VECTOR_URL", "INGEST_URL", "MONITOR_URL")


def cluster_key() -> str:
    return (get_settings().CLUSTER_KEY or "").strip()


def _peer_hosts() -> set[str]:
    s = get_settings()
    out = set()
    for attr in PEER_URL_SETTINGS:
        u = urlsplit(getattr(s, attr, "") or "")
        if u.hostname:
            out.add(f"{u.hostname}:{u.port or (443 if u.scheme == 'https' else 80)}")
    return out


def is_peer(url: httpx.URL) -> bool:
    return f"{url.host}:{url.port or (443 if url.scheme == 'https' else 80)}" in _peer_hosts()


def headers_for(url: str | httpx.URL) -> dict[str, str]:
    key = cluster_key()
    return {HEADER: key} if key and is_peer(httpx.URL(str(url))) else {}


def _add_key(request: httpx.Request) -> None:
    key = cluster_key()
    if key and HEADER not in request.headers and is_peer(request.url):
        request.headers[HEADER] = key


async def _add_key_async(request: httpx.Request) -> None:
    _add_key(request)


_installed = False


def install_httpx_hook() -> None:
    """Patch httpx.Client / AsyncClient so every instance adds the key to requests aimed at our services."""
    global _installed
    if _installed:
        return
    _installed = True
    for cls, hook in ((httpx.Client, _add_key), (httpx.AsyncClient, _add_key_async)):
        orig = cls.__init__

        def init(self, *a, __orig=orig, __hook=hook, **kw):
            hooks = dict(kw.pop("event_hooks", None) or {})
            hooks["request"] = list(hooks.get("request", [])) + [__hook]
            __orig(self, *a, event_hooks=hooks, **kw)
        cls.__init__ = init


class ClusterKeyMiddleware:
    """Pure ASGI middleware so it covers both HTTP and WebSocket scopes."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        key = cluster_key()
        if not key or scope["type"] not in ("http", "websocket") or scope.get("path", "").startswith(OPEN_PATHS):
            return await self.app(scope, receive, send)
        if scope["type"] == "http" and scope.get("method") == "OPTIONS":      # CORS preflight carries no custom header
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        given = headers.get(HEADER.lower()) or (parse_qs(scope.get("query_string", b"").decode()).get("key") or [""])[0]
        if hmac.compare_digest(given.encode(), key.encode()):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        body = b'{"detail":"missing or wrong X-Cluster-Key"}'
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
