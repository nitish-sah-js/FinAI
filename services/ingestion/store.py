"""Parquet/JSON store + as_of filtering."""
from __future__ import annotations
import json, os, time
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from .timeutil import parse_dt


def data_dir() -> Path:
    """Same folder copilot_common.cache writes to (<repo>/data, or DATA_DIR), independent of the cwd."""
    from copilot_common.settings import get_settings
    return get_settings().data_dir


def _safe(t: str) -> str:
    return t.replace("^", "_").replace("=", "_").replace("/", "_")


def prices_path(ticker: str, interval: str) -> Path:
    d = data_dir() / "cache/ingestion/prices"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{_safe(ticker)}_{interval}.parquet"


_PQ: dict[Path, tuple[float, pd.DataFrame]] = {}


def _read(p: Path) -> pd.DataFrame:
    """read_parquet memoised on (path, mtime): warm /prices stays well under 100 ms (10 tickers)."""
    m = p.stat().st_mtime
    hit = _PQ.get(p)
    if hit is None or hit[0] != m:
        hit = _PQ[p] = (m, pd.read_parquet(p))
    return hit[1]


def save_prices(ticker: str, interval: str, df: pd.DataFrame) -> None:
    """Incremental: appends new rows, new values win on duplicate index."""
    if df is None or df.empty:
        return
    p = prices_path(ticker, interval)
    if p.exists():
        df = pd.concat([_read(p), df])
        df = df[~df.index.duplicated(keep="last")].sort_index()
    df.to_parquet(p)


def load_prices(ticker: str, interval: str, as_of: datetime | None = None) -> pd.DataFrame | None:
    p = prices_path(ticker, interval)
    if not p.exists():
        return None
    df = _read(p)
    if as_of is not None:
        cutoff = as_of.astimezone(timezone.utc).replace(tzinfo=None)
        df = df[df.index <= pd.Timestamp(cutoff)]
    return df


def prices_fresh(ticker: str, interval: str, ttl_s: int, period_days: int | None,
                 start: pd.Timestamp | None = None, end: pd.Timestamp | None = None) -> bool:
    p = prices_path(ticker, interval)
    if not p.exists():
        return False
    if start is not None:  # explicit window (start/end or as_of): the stored rows must cover it
        df = _read(p)
        stop = end if end is not None else pd.Timestamp.now().normalize()
        w = df[(df.index >= start) & (df.index < stop + pd.Timedelta(days=1))]
        if interval == "1d" and len(w) < 0.6 * len(pd.bdate_range(start, stop)):
            return False
        if stop < pd.Timestamp.now().normalize() - pd.Timedelta(days=3):
            return True   # a finished historical window never goes stale
    if time.time() - p.stat().st_mtime > ttl_s:
        return False
    if interval == "1d" and period_days and start is None:
        df = _read(p)
        if df.empty or (df.index[-1] - df.index[0]).days < period_days * 0.8:
            return False
    return True


def filter_as_of(items: list[dict], as_of: datetime | None, key: str = "published_at") -> list[dict]:
    if as_of is None:
        return items
    return [i for i in items if parse_dt(i[key]) <= as_of]


# ---- last-good values (used for degraded / chaos fallbacks) ----
def _lg(tool: str, key: str) -> Path:
    d = data_dir() / "cache/ingestion/last_good"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{tool}_{_safe(key)}.json"


def save_last_good(tool: str, key: str, value: dict, as_of: datetime, confidence: float | None) -> None:
    _lg(tool, key).write_text(json.dumps(
        {"value": value, "as_of": as_of.isoformat(), "confidence": confidence}, default=str))


def load_last_good(tool: str, key: str) -> dict | None:
    p = _lg(tool, key)
    return json.loads(p.read_text()) if p.exists() else None
