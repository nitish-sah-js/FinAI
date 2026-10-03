from __future__ import annotations
import asyncio
import json
import os
from datetime import date, timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from copilot_common.cache import CacheMiss, cached
from copilot_common.service_base import get_run_id
from copilot_common.settings import get_data_dir, settings


class DataError(ValueError):
    """Not enough / no usable data. Endpoints turn this into degraded evidence (never an HTTP error)."""


# ---------------------------------------------------------------- data files
def load_json(name: str) -> dict:
    """data/<name> from DATA_DIR, nearest ./data, or the repo root above this file. {} if missing."""
    cands = [get_data_dir() / name]
    for p in Path(__file__).resolve().parents:
        cands.append(p / "data" / name)
    for c in cands:
        if c.exists():
            try:
                return json.loads(c.read_text())
            except Exception:
                return {}
    return {}


# ---------------------------------------------------------------- price sources
async def _fetch_ingest(tickers: list[str], start: date, end: date, as_of: date | None) -> tuple[dict[str, pd.Series], set[str]]:
    from copilot_common.models import ToolResult
    body = {"tickers": tickers, "start": start.isoformat(), "end": end.isoformat(),
            "as_of": as_of.isoformat() if as_of else None}
    headers = {"X-Run-Id": get_run_id()} if get_run_id() else {}
    url = f"{settings.INGEST_URL}/prices"
    # skip a known-down ingestion instantly: Windows takes ~2 s to refuse each connection (measured VaR 2.3–4.6 s → <1 s)
    from copilot_common import reachability
    if reachability.is_down(url):
        raise httpx.ConnectError("ingestion marked down")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=reachability.CONNECT_TIMEOUT_S)) as c:
            r = await c.post(url, json=body, headers=headers)
            r.raise_for_status()
    except (httpx.ConnectError, httpx.ConnectTimeout):
        reachability.mark_down(url)
        raise
    res = ToolResult.model_validate(r.json())
    frames: dict[str, pd.Series] = {}
    deg: set[str] = set()
    for ev in res.evidence:
        if ev.tool != "prices":
            continue
        t = ev.value.get("ticker")
        rows = ev.value.get("rows") or []
        if not t or not rows:
            continue
        idx = pd.to_datetime([str(x["date"])[:10] for x in rows])
        vals = [x.get("adj_close", x.get("close")) for x in rows]
        s = pd.Series(vals, index=idx, dtype="float64").dropna()
        s = s[~s.index.duplicated(keep="last")].sort_index()
        if len(s):
            frames[t] = s
            if ev.degraded:
                deg.add(t)
    return frames, deg


def _download_yf(tickers: list[str], start: str, end: str) -> dict[str, dict[str, float]]:
    import yfinance as yf  # lazy: heavy import, network
    raw = yf.download(tickers, start=start, end=end, auto_adjust=True, progress=False, threads=False)
    if raw is None or len(raw) == 0:
        return {}
    close = raw["Close"] if "Close" in raw else raw
    if isinstance(close, pd.Series):
        close = close.to_frame(tickers[0])
    idx = pd.DatetimeIndex(close.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    close.index = idx.normalize()
    out: dict[str, dict[str, float]] = {}
    for t in close.columns:
        s = close[t].dropna()
        if len(s):
            out[str(t)] = {d.strftime("%Y-%m-%d"): float(v) for d, v in s.items()}
    return out


async def _fetch_yf(tickers: list[str], start: date, end: date) -> dict[str, pd.Series]:
    key = {"fn": "yf_download", "tickers": sorted(tickers), "start": start.isoformat(), "end": end.isoformat()}
    try:
        raw = await cached("quant", key, lambda: asyncio.to_thread(
            _download_yf, tickers, start.isoformat(), (end + timedelta(days=1)).isoformat()))
    except CacheMiss:
        return {}
    except Exception:
        return {}
    out = {}
    for t, d in (raw or {}).items():
        s = pd.Series(d, dtype="float64")
        s.index = pd.to_datetime(s.index)
        out[t] = s.sort_index()
    return out


async def get_prices(tickers: list[str], start: date, end: date, as_of: date | None = None) -> tuple[pd.DataFrame, set[str]]:
    """Wide DataFrame (index=date, columns=tickers, adj close) truncated to <= min(end, as_of),
    plus the set of DEGRADED tickers (served from yfinance fallback/cache, ingestion-degraded, or missing)."""
    tickers = list(dict.fromkeys(tickers))
    cutoff = min(end, as_of) if as_of else end
    frames: dict[str, pd.Series] = {}
    degraded: set[str] = set()
    try:
        frames, degraded = await _fetch_ingest(tickers, start, cutoff, as_of)
    except Exception:
        frames, degraded = {}, set()
    missing = [t for t in tickers if t not in frames]
    if missing:
        yf_frames = await _fetch_yf(missing, start, cutoff)
        for t, s in yf_frames.items():
            frames[t] = s
        degraded |= set(missing)          # fallback path or no data at all -> degraded
    df = pd.DataFrame(frames)
    if df.empty:
        return pd.DataFrame(columns=[]), degraded
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[df.index <= pd.Timestamp(cutoff)]          # time machine: never look past as_of
    df = df[df.index >= pd.Timestamp(start)]
    df = df.reindex(columns=[t for t in tickers if t in df.columns])
    return df, degraded


def latest_prices(df: pd.DataFrame) -> dict[str, float]:
    return {c: float(df[c].dropna().iloc[-1]) for c in df.columns if df[c].notna().any()}


def lookback_start(end: date, rows: int) -> date:
    """Calendar start date that comfortably covers `rows` trading days."""
    return end - timedelta(days=int(rows * 1.55) + 20)
