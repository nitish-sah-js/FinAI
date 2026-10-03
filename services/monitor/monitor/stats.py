"""Turn raw buffers (5-minute bars, news items) into detector inputs (11 §3 formulas)."""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd


def price_inputs(bars: dict[str, pd.DataFrame], days: int = 20) -> list[dict]:
    """bars[ticker]: DataFrame indexed by tz-aware ts with columns close, volume (5-minute bars).
    r_5m = last bar return; σ_5m = std of 5-minute returns over the previous `days` trading days (last bar excluded)."""
    out = []
    for t, df in bars.items():
        df = df.dropna(subset=["close"])
        if len(df) < 30:
            continue
        day = pd.Series([ts.date() for ts in df.index], index=df.index)
        r = df["close"].groupby(day).pct_change()          # within-day returns only (no overnight gap)
        dates = sorted(set(day))
        if pd.isna(r.iloc[-1]):                            # last bar is the day's first bar
            continue
        hist = r.iloc[:-1][day.iloc[:-1].isin(set(dates[-days - 1:]))].dropna()
        if len(hist) < 30:
            continue
        out.append({"ticker": t, "r_5m": float(r.iloc[-1]), "sigma_5m": float(hist.std()),
                    "history_days": len(dates) - 1, "ts": df.index[-1].isoformat()})
    return out


def volume_inputs(bars: dict[str, pd.DataFrame], days: int = 20, window: int = 6) -> list[dict]:
    """V_30m = volume of the last 6 bars; baseline = log V_30m over the same time-of-day window on previous days."""
    out = []
    for t, df in bars.items():
        df = df.dropna(subset=["volume"])
        if len(df) < window * 3:
            continue
        last_ts = df.index[-1]
        today = df[df.index.date == last_ts.date()]
        if len(today) < window:
            continue
        v_now = float(today["volume"].iloc[-window:].sum())
        tod = last_ts.time()
        hist = []
        for d in sorted({ts.date() for ts in df.index if ts.date() < last_ts.date()})[-days:]:
            day = df[(df.index.date == d) & (df.index.time <= tod)]
            if len(day) >= window:
                v = float(day["volume"].iloc[-window:].sum())
                if v > 0:
                    hist.append(math.log(v))
        if len(hist) < 5 or np.std(hist) == 0:
            continue
        out.append({"ticker": t, "v_30m": v_now, "mean_log_v": float(np.mean(hist)), "std_log_v": float(np.std(hist)),
                    "n_days": len(hist)})
    return out


def _ts(x) -> datetime | None:
    try:
        return pd.Timestamp(x).tz_convert("UTC").to_pydatetime() if pd.Timestamp(x).tzinfo else \
            pd.Timestamp(x).tz_localize("UTC").to_pydatetime()
    except (ValueError, TypeError):
        return None


def news_inputs(items: list[dict], tickers: set[str], now: datetime, sector_words: dict[str, list[str]] | None = None
                ) -> list[dict]:
    """k = items about the ticker in the last 60 min; λ = hourly mean over the previous 7 days.
    Tagged = ticker in item.tickers; otherwise a sector keyword in the title counts with relevance 0.5."""
    sector_words = sector_words or {}
    out = []
    for t in tickers:
        words = [w.lower() for w in sector_words.get(t, [])]
        recent, week, tagged_any, titles = 0, 0, False, []
        for it in items:
            ts = _ts(it.get("published_at"))
            if ts is None or ts > now or ts < now - timedelta(days=7, hours=1):
                continue
            tagged = t in (it.get("tickers") or [])
            sector = bool(words) and any(w in (it.get("title") or "").lower() for w in words)
            if not (tagged or sector):
                continue
            if ts >= now - timedelta(hours=1):
                recent += 1
                tagged_any |= tagged
                titles.append(it.get("title", ""))
            else:
                week += 1
        if recent:
            out.append({"ticker": t, "k": recent, "lambda_h": week / (7 * 24), "tagged": tagged_any, "titles": titles})
    return out


def score_of(it: dict) -> float | None:
    """FinBERT label → signed score: positive = +conf, negative = −conf, neutral = 0."""
    lab, conf = (it.get("sentiment_label") or "").lower(), it.get("sentiment_confidence")
    if not lab:
        return None
    if lab == "neutral":
        return 0.0
    c = float(conf if conf is not None else abs(it.get("sentiment_score") or 0.5))
    return c if lab == "positive" else -c if lab == "negative" else None


def sentiment_inputs(items: list[dict], tickers: set[str], now: datetime) -> list[dict]:
    """Δ = mean(score, last 6 h) − mean(score, the 24 h before that)."""
    out = []
    for t in tickers:
        last6, prior, confs = [], [], []
        for it in items:
            if t not in (it.get("tickers") or []):
                continue
            ts, s = _ts(it.get("published_at")), score_of(it)
            if ts is None or s is None or ts > now:
                continue
            if ts >= now - timedelta(hours=6):
                last6.append(s)
                if it.get("sentiment_confidence") is not None:
                    confs.append(float(it["sentiment_confidence"]))
            elif ts >= now - timedelta(hours=30):
                prior.append(s)
        if last6 and prior:
            out.append({"ticker": t, "mean_6h": float(np.mean(last6)), "mean_24h": float(np.mean(prior)),
                        "n_6h": len(last6), "conf": float(np.mean(confs)) if confs else 0.5})
    return out
