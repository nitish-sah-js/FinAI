from __future__ import annotations
import asyncio
import pandas as pd


def _download(tickers: list[str], period: str, interval: str, start: str | None = None, end: str | None = None):
    import yfinance as yf
    window = {"start": start, "end": end} if start else {"period": period}
    return yf.download(tickers, interval=interval, auto_adjust=True,
                       group_by="ticker", threads=True, progress=False, **window)


async def fetch_prices(tickers: list[str], period: str, interval: str,
                       start: str | None = None, end: str | None = None) -> dict[str, pd.DataFrame]:
    """ONE batch call for all tickers (never loop single calls). Index = naive UTC.
    start/end (YYYY-MM-DD, end exclusive as in yfinance) override period: used for as_of / historical windows."""
    df = await asyncio.to_thread(_download, tickers, period, interval, start, end)
    out: dict[str, pd.DataFrame] = {}
    if df is None or df.empty:
        return out
    for t in tickers:
        sub = None
        if isinstance(df.columns, pd.MultiIndex):
            if t in df.columns.get_level_values(0):
                sub = df[t]
            elif len(tickers) == 1:
                sub = df.droplevel(1, axis=1) if df.columns.nlevels == 2 else df
        elif len(tickers) == 1:
            sub = df
        if sub is None:
            continue
        sub = sub.copy()
        sub.columns = [str(c).lower() for c in sub.columns]
        if "close" not in sub.columns:
            continue
        sub = sub.dropna(subset=["close"])
        idx = sub.index
        if getattr(idx, "tz", None) is not None:
            sub.index = idx.tz_convert("UTC").tz_localize(None)
        sub = sub[["open", "high", "low", "close", "volume"]].fillna({"volume": 0})
        if not sub.empty:
            out[t] = sub
    return out
