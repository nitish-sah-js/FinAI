"""Upstream calls (ingestion 10, agri 09). Every call is short-timeout, circuit-broken and never raises:
failures return None and are recorded in DEPS for /health and /monitor/state."""
from __future__ import annotations

import asyncio
import logging

import httpx
import pandas as pd

from copilot_common import reachability
from copilot_common.settings import get_settings

log = logging.getLogger("monitor.sources")
DEPS: dict[str, str] = {"ingestion": "unknown", "agri": "unknown", "prices": "unknown"}


async def _call(dep: str, method: str, url: str, timeout: float = 8.0, **kw) -> dict | list | None:
    if reachability.is_down(url):
        DEPS[dep] = "down"
        return None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=reachability.CONNECT_TIMEOUT_S)) as c:
            r = await c.request(method, url, **kw)
        reachability.mark_up(url)
        r.raise_for_status()
        DEPS[dep] = "ok"
        return r.json()
    except (httpx.HTTPError, ValueError) as e:
        if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
            reachability.mark_down(url)
        DEPS[dep] = "down"
        log.debug("%s %s failed: %s", method, url, type(e).__name__)
        return None


async def feed_since(ts: str | None) -> tuple[list[dict], list[dict]] | None:
    """GET {INGEST_URL}/feed/since → (news items, price ticks). Accepts a flat list (items with type/news_id vs
    ticker+close) or {"items"|"news": [...], "ticks": [...]}."""
    url = get_settings().INGEST_URL.rstrip("/") + "/feed/since"
    data = await _call("ingestion", "GET", url, params={"ts": ts} if ts else None)
    if data is None:
        return None
    if isinstance(data, dict):
        return list(data.get("items") or data.get("news") or []), list(data.get("ticks") or [])
    news = [d for d in data if d.get("type") == "news" or "news_id" in d]
    ticks = [d for d in data if d.get("type") == "tick" or ("close" in d and "ticker" in d)]
    return news, ticks


async def weather(region_id: str) -> dict | None:
    url = get_settings().INGEST_URL.rstrip("/") + "/weather/features"
    data = await _call("ingestion", "POST", url, json={"region_id": region_id, "horizon_days": 5, "as_of": None})
    ev = (data or {}).get("evidence") or []
    return ev[0] if ev else None


async def agri(region_id: str) -> dict | None:
    url = get_settings().AGRI_URL.rstrip("/") + "/agri_signal"
    data = await _call("agri", "POST", url, json={"region_id": region_id, "date": None, "crop_season": None,
                                                  "as_of": None})
    ev = (data or {}).get("evidence") or []
    return ev[0] if ev else None


def _bars_from_rows(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    tcol = next((c for c in ("ts", "datetime", "date") if c in df.columns), None)
    if tcol is None or "close" not in df.columns:
        return pd.DataFrame(columns=["close", "volume"])
    idx = pd.to_datetime(df[tcol], utc=True)
    out = pd.DataFrame({"close": pd.to_numeric(df["close"], errors="coerce"),
                        "volume": pd.to_numeric(df.get("volume", 0), errors="coerce")})
    out.index = idx
    return out.sort_index()


def _yf_bars(tickers: list[str], period: str) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    raw = yf.download(tickers, period=period, interval="5m", group_by="ticker", auto_adjust=False,
                      progress=False, threads=True)
    out = {}
    for t in tickers:
        try:
            sub = raw[t] if len(tickers) > 1 else raw
            df = sub[["Close", "Volume"]].rename(columns={"Close": "close", "Volume": "volume"}).dropna(subset=["close"])
        except (KeyError, TypeError):
            continue
        if len(df):
            df.index = pd.to_datetime(df.index, utc=True)
            out[t] = df
    return out


async def bars_5m(tickers: list[str], period: str = "30d") -> tuple[dict[str, pd.DataFrame], str]:
    """5-minute bars: ingestion /prices first, then yfinance (≤ 60 d for 5m). Returns (bars, source)."""
    url = get_settings().INGEST_URL.rstrip("/") + "/prices"
    data = await _call("ingestion", "POST", url, timeout=20,
                       json={"tickers": tickers, "period": period, "interval": "5m", "as_of": None})
    out: dict[str, pd.DataFrame] = {}
    for ev in (data or {}).get("evidence") or []:
        v = ev.get("value") or {}
        if v.get("ticker") and v.get("rows") and not ev.get("degraded"):
            out[v["ticker"]] = _bars_from_rows(v["rows"])
    missing = [t for t in tickers if t not in out]
    if not missing:
        DEPS["prices"] = "ok"
        return out, "ingestion"
    try:
        out.update(await asyncio.wait_for(asyncio.to_thread(_yf_bars, missing, period), 60))
        DEPS["prices"] = "ok" if out else "down"
    except Exception as e:  # noqa: BLE001
        DEPS["prices"] = "down"
        log.warning("yfinance 5m bars failed: %s", type(e).__name__)
    return out, ("ingestion+yfinance" if len(out) > len(missing) else "yfinance")


def merge_ticks(bars: dict[str, pd.DataFrame], ticks: list[dict]) -> None:
    """Append ingestion price ticks {ticker, ts, close, volume} to the bar buffers (in place)."""
    by_t: dict[str, list[dict]] = {}
    for tk in ticks:
        by_t.setdefault(tk["ticker"], []).append(tk)
    for t, rows in by_t.items():
        new = _bars_from_rows(rows)
        bars[t] = pd.concat([bars.get(t, new.iloc[:0]), new])
        bars[t] = bars[t][~bars[t].index.duplicated(keep="last")].sort_index().tail(80 * 61)
