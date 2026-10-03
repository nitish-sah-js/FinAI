"""Daily closes for any service: ingestion POST /prices (10) first, yfinance directly as the fallback.

Used by the backtest (realized returns, price-only baseline) and paper trading (marks). Results go through the
shared cache, so with CACHE_MODE=record a rerun is free and identical (12 §A3.4); CACHE_MODE=replay never touches
the network.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import httpx

from . import reachability
from .cache import CacheMiss, cached
from .settings import get_settings


class PriceUnavailable(RuntimeError):
    pass


def _parse_rows(payload: dict, symbol: str) -> list[tuple[str, float]]:
    """ToolResult from /prices: one Evidence per ticker, value.rows = [{date, close, ...}]."""
    rows: list = []
    for ev in payload.get("evidence", []):
        v = ev.get("value") or {}
        if v.get("ticker") in (None, symbol):
            rows = v.get("rows", [])
            break
    return sorted((str(b["date"])[:10], float(b["close"])) for b in rows if b.get("close") is not None)


async def _from_ingestion(symbol: str, start: str, end: str, as_of: str | None) -> list[tuple[str, float]]:
    url = get_settings().INGEST_URL.rstrip("/") + "/prices"
    if reachability.is_down(url):
        raise PriceUnavailable("ingestion marked down")
    body = {"tickers": [symbol], "ticker": symbol, "interval": "1d", "start": start, "end": end, "as_of": as_of}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=reachability.CONNECT_TIMEOUT_S)) as c:
            r = await c.post(url, json=body)
            r.raise_for_status()
            rows = _parse_rows(r.json(), symbol)
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        reachability.mark_down(url)
        raise PriceUnavailable(f"ingestion unreachable: {type(e).__name__}") from e
    except (httpx.HTTPError, ValueError, KeyError) as e:
        raise PriceUnavailable(f"ingestion error: {type(e).__name__}") from e
    if not rows:
        raise PriceUnavailable(f"ingestion returned no rows for {symbol}")
    return rows


def _from_yfinance(symbol: str, start: str, end: str) -> list[tuple[str, float]]:
    try:
        import yfinance as yf
    except ImportError as e:
        raise PriceUnavailable("ingestion down and yfinance not installed") from e
    # yfinance's `end` is exclusive
    end_x = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
    try:
        h = yf.Ticker(symbol).history(start=start, end=end_x, interval="1d", auto_adjust=False)
    except Exception as e:  # noqa: BLE001  yfinance raises many types
        raise PriceUnavailable(f"yfinance failed for {symbol}: {type(e).__name__}") from e
    rows = [(idx.date().isoformat(), float(c)) for idx, c in h["Close"].items() if c == c] if len(h) else []
    if not rows:
        raise PriceUnavailable(f"no yfinance data for {symbol} {start}..{end}")
    return rows


async def get_closes(symbol: str, start: str, end: str, as_of: str | None = None) -> tuple[list[tuple[str, float]], str]:
    """Return ([(YYYY-MM-DD, close)] sorted, source). Rows after `as_of` are dropped (time machine)."""
    async def fetch():
        try:
            return {"rows": await _from_ingestion(symbol, start, end, as_of), "source": "ingestion /prices"}
        except PriceUnavailable:
            rows = await asyncio.to_thread(_from_yfinance, symbol, start, end)
            return {"rows": rows, "source": "yfinance (direct; ingestion unavailable)"}

    key = {"fn": "get_closes", "symbol": symbol, "start": start, "end": end}
    # only fully past windows are cached: a window ending today would freeze an intraday price
    cacheable = date.fromisoformat(end) < date.today()
    try:
        data = await cached("prices", key, fetch) if cacheable else await fetch()
    except CacheMiss as e:
        raise PriceUnavailable(f"replay cache miss for {symbol} {start}..{end}") from e
    rows = [(d, float(c)) for d, c in data["rows"]]
    if as_of:
        rows = [(d, c) for d, c in rows if d <= str(as_of)[:10]]
    return rows, data["source"]


def get_closes_sync(symbol: str, start: str, end: str, as_of: str | None = None) -> tuple[list[tuple[str, float]], str]:
    """Sync wrapper that also works when called from inside a running event loop (runs on a helper thread)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(get_closes(symbol, start, end, as_of))
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, get_closes(symbol, start, end, as_of)).result()
