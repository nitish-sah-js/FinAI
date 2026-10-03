"""Realized returns from {INGEST_URL}/prices WITHOUT as_of (the only place future data is used)."""
from __future__ import annotations
import os
import httpx

INGEST_URL = os.getenv("INGEST_URL", "http://localhost:8201")


def parse_bars(payload) -> list[tuple[str, float]]:
    """POST /prices returns ToolResult: evidence[0].value.rows = [{date,open,high,low,close,volume}]."""
    if isinstance(payload, dict) and "evidence" in payload:
        rows = payload["evidence"][0]["value"]["rows"] if payload["evidence"] else []
    else:
        rows = payload.get("rows", []) if isinstance(payload, dict) else payload
    return sorted((str(b["date"])[:10], float(b["close"])) for b in rows if b.get("close") is not None)


def fetch_bars(symbol: str, start: str, end: str, as_of: str | None = None, base_url: str | None = None) -> list[tuple[str, float]]:
    body = {"ticker": symbol, "interval": "1d", "start": start, "end": end}
    if as_of:
        body["as_of"] = as_of
    r = httpx.post(f"{base_url or INGEST_URL}/prices", json=body, timeout=30)
    r.raise_for_status()
    return parse_bars(r.json())


def realized_from_closes(closes: list[tuple[str, float]], as_of: str, h: int) -> float | None:
    """base = close of last trading day <= as_of; target = h trading days after base.
    (Equivalent to spec close[t0+h]/close[t0-1]-1 where t0-1 is the base day and t0 is the first
    trading day after it, counting the t0 session as day 1.)"""
    idx = [i for i, (d, _) in enumerate(closes) if d <= as_of[:10]]
    if not idx:
        return None
    b = idx[-1]
    if b + h >= len(closes):
        return None
    return closes[b + h][1] / closes[b][1] - 1


def realized_return(asset: str, as_of: str, h: int, base_url: str | None = None) -> float | None:
    from datetime import date, timedelta
    d = date.fromisoformat(as_of[:10])
    bars = fetch_bars(asset, (d - timedelta(days=10)).isoformat(), (d + timedelta(days=int(h * 2.5) + 10)).isoformat(),
                      base_url=base_url)
    return realized_from_closes(bars, as_of, h)
