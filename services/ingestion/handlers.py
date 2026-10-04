"""Endpoint logic. Each handler: (req, rid, counter, t0, chaos) -> ToolResult."""
from __future__ import annotations
import asyncio
from datetime import timedelta

import pandas as pd
from fastapi import HTTPException
from copilot_common.models import ToolResult

from . import store
from .features.weather_features import (heat_index_c, rain_anomaly_pct, soil_clim_stats, soil_moisture_z,
                                        weather_alerts, weather_confidence, window_clim_mm)
from .sources import announcements, gdelt, macro, openmeteo, prices_yf, rss
from .sources import storms as storm_src
from .timeutil import iso, parse_as_of, parse_dt, utcnow
from .util import CacheMiss, cg, degraded_ev, is_replay, load_json, mk_ev

PERIOD_DAYS = {"1d": 1, "5d": 5, "1mo": 31, "3mo": 93, "6mo": 186, "1y": 366, "2y": 731, "5y": 1827,
               "10y": 3653, "max": None}


# ------------------------------------------------------------------ prices
def price_window(period: str, start: str | None, end: str | None, as_of) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """(start, end) dates, end inclusive. as_of caps end and, without an explicit start, the window is
    `period` back from as_of (so a 2021 backtest downloads 2021 data, not the last year)."""
    e = pd.Timestamp(end) if end else None
    if as_of is not None:
        cap = pd.Timestamp(as_of.date())
        e = cap if e is None else min(e, cap)
    days = PERIOD_DAYS.get(period)
    s = pd.Timestamp(start) if start else (e - pd.Timedelta(days=days) if e is not None and days else None)
    return s, e


async def ensure_prices(tickers: list[str], period: str, interval: str, ttl: int | None = None,
                        start: pd.Timestamp | None = None, end: pd.Timestamp | None = None) -> list[str]:
    """Batch-download only stale tickers (one yf.download call), save incrementally. Returns warnings."""
    ttl = ttl or (900 if interval != "1d" else 43200)
    need = [t for t in tickers if not store.prices_fresh(t, interval, ttl, PERIOD_DAYS.get(period), start, end)]
    if not need or is_replay():
        return []
    window = {}
    if start is not None:
        stop = end if end is not None else pd.Timestamp.now().normalize()
        window = {"start": start.strftime("%Y-%m-%d"), "end": (stop + pd.Timedelta(days=1)).strftime("%Y-%m-%d")}
    try:
        for t, df in (await prices_yf.fetch_prices(need, period, interval, **window)).items():
            store.save_prices(t, interval, df)
        return [f"no data for {t}" for t in need if not store.prices_path(t, interval).exists()]
    except Exception as e:
        return [f"yfinance failed: {e}"]


def _rows(df: pd.DataFrame, interval: str) -> list[dict]:
    fmt = (lambda ts: ts.strftime("%Y-%m-%d")) if interval == "1d" else (lambda ts: ts.strftime("%Y-%m-%dT%H:%M:%SZ"))
    return [{"date": fmt(ts), "open": round(float(r.open), 4), "high": round(float(r.high), 4),
             "low": round(float(r.low), 4), "close": round(float(r.close), 4), "volume": int(r.volume)}
            for ts, r in df.iterrows()]


async def prices_handler(req, rid, counter, t0, chaos) -> ToolResult:
    as_of = parse_as_of(req.as_of)
    start, end = price_window(req.period, req.start, req.end, as_of)
    warnings = await ensure_prices(req.tickers, req.period, req.interval, start=start, end=end)
    evs = []
    for t in req.tickers:
        df = store.load_prices(t, req.interval, as_of)
        if df is not None and end is not None:
            df = df[df.index < end + pd.Timedelta(days=1)]
        if df is not None and start is not None:
            df = df[df.index >= start]
        if df is None or df.empty:
            evs.append(degraded_ev(counter, rid, "prices", t, "yfinance",
                                   "cache_miss" if is_replay() else "no_data", f"{t}: no price data",
                                   {"ticker": t, "interval": req.interval, "rows": []}, t0=t0, as_of=as_of))
            continue
        days = PERIOD_DAYS.get(req.period)
        if days and start is None:
            df = df[df.index >= df.index[-1] - pd.Timedelta(days=days)]
        rows = _rows(df, req.interval)
        last = df.index[-1].to_pydatetime().replace(tzinfo=None)
        summ = f"{t}: close {rows[-1]['close']:.2f} on {rows[-1]['date']}"
        if len(rows) > 1:
            summ += f" ({rows[-1]['close'] / rows[-2]['close'] - 1:+.1%} last bar)"
        evs.append(mk_ev(counter, rid, "prices", {"ticker": t, "interval": req.interval, "rows": rows},
                         "yfinance", parse_dt(last), summary=summ, confidence=0.95, t0=t0,
                         source_url="https://finance.yahoo.com/quote/" + t))
    return ToolResult(evidence=evs, warnings=warnings)


# ------------------------------------------------------------------ news
async def _collect_news(query, as_of, since_hours, warns) -> list[dict]:
    tickers_cfg, feeds = load_json("tickers.json"), load_json("rss_feeds.json")
    end = as_of or utcnow()
    items: list[dict] = []
    if as_of is None:
        try:
            rss_items = await cg("rss", {"feeds": [f["url"] for f in feeds]},
                                 lambda: rss.fetch_feeds(feeds, tickers_cfg), ttl=300)
            if query:
                terms = query.lower().split()
                rss_items = [i for i in rss_items if all(t in f"{i['title']} {i['summary']}".lower() for t in terms)]
            items += rss_items
        except Exception as e:
            warns.append(f"rss: {type(e).__name__}")
    if query:
        start = end - timedelta(hours=since_hours)
        country = "US" if any(w in query.lower() for w in ("hurricane", "gulf", "texas", "louisiana")) else "IN"
        try:
            kw = dict(start=start, end=end) if as_of else dict(timespan=f"{since_hours}h")
            items += await cg("gdelt", {"q": query, "c": country, **{k: str(v) for k, v in kw.items()}},
                              lambda: gdelt.search(query, tickers_cfg, country=country, **kw), ttl=300)
        except Exception as e:
            warns.append(f"gdelt: {type(e).__name__}")
    return items


async def news_handler(req, rid, counter, t0, chaos) -> ToolResult:
    as_of, warns = parse_as_of(req.as_of), []
    now = as_of or utcnow()
    items = await _collect_news(req.query, as_of, req.since_hours, warns)
    items = store.filter_as_of(items, as_of)
    items = [i for i in items if parse_dt(i["published_at"]) >= now - timedelta(hours=req.since_hours)]
    if req.tickers:
        want = set(req.tickers)
        items = [i for i in items if want & set(i["tickers"])]
    items = rss.dedupe(sorted(items, key=lambda i: i["published_at"], reverse=True))[: req.limit]
    all_failed = not items and bool(warns)
    last = max((parse_dt(i["published_at"]) for i in items), default=now)
    return ToolResult(evidence=[mk_ev(
        counter, rid, "news", {"items": items}, "RSS (ET, Mint, BS, Moneycontrol) + GDELT DOC 2.0", last,
        summary=f"{len(items)} items, {req.since_hours}h, query '{req.query or ''}'",
        degraded=all_failed, reason="cache_miss" if all_failed and is_replay() else ("api_timeout" if all_failed else None),
        t0=t0)], warnings=warns)


async def announcements_handler(req, rid, counter, t0, chaos) -> ToolResult:
    items, warns = [], []
    for t in req.tickers:
        sym = t.replace(".NS", "")
        try:
            items += await cg("nse", {"s": sym, "h": req.since_hours},
                              lambda sym=sym: announcements.fetch(sym, req.since_hours), ttl=900)
        except Exception as e:
            warns.append(f"NSE {sym}: {type(e).__name__} (best effort; try fixtures)")
    items = store.filter_as_of(items, parse_as_of(getattr(req, "as_of", None)))
    items.sort(key=lambda i: i["published_at"], reverse=True)
    return ToolResult(evidence=[mk_ev(
        counter, rid, "news", {"items": items}, "NSE corporate announcements (best effort)",
        max((parse_dt(i["published_at"]) for i in items), default=utcnow()),
        summary=f"{len(items)} announcements for {', '.join(req.tickers)}", degraded=bool(warns and not items),
        reason="api_timeout" if warns and not items else None, t0=t0)], warnings=warns)


# ------------------------------------------------------------------ macro
async def macro_handler(req, rid, counter, t0, chaos) -> ToolResult:
    as_of = parse_as_of(req.as_of)
    start, end = price_window("3mo", None, None, as_of)   # as_of -> the 3 months before as_of
    warns = await ensure_prices(["INR=X", "BZ=F"], "3mo", "1d", ttl=21600, start=start, end=end)
    ld = lambda t: store.load_prices(t, "1d", as_of)
    col = lambda df: df["close"] if df is not None and not df.empty else None
    try:
        pairs = await cg("fred", {"id": "DGS10", "from": "2000"}, lambda: macro.fred_series("DGS10"), ttl=21600)
        us10y = macro.to_series(pairs)
    except Exception as e:
        us10y = None
        warns.append(f"FRED: {type(e).__name__}")
    value, dates, w2, n_missing = macro.build_macro(col(ld("INR=X")), col(ld("BZ=F")), us10y,
                                                    macro.load_policy(store_cfg()), macro.load_cpi(store_cfg()), as_of)
    mdates = [pd.Timestamp(dates[k]) for k in ("usd_inr", "brent", "us10y") if k in dates]
    as_ev = parse_dt(max(mdates).to_pydatetime()) if mdates else utcnow()
    parts = []
    if value["brent_chg_5d"] is not None: parts.append(f"Brent {value['brent_chg_5d']:+.1%} in 5d")
    if value["usd_inr_chg_5d"] is not None: parts.append(f"USD/INR {value['usd_inr_chg_5d']:+.1%}")
    if value["repo_rate"] is not None: parts.append(f"repo {value['repo_rate']}% (last {value['repo_change_bps']:+d}bp)")
    pol_date = dates.get("repo_rate", "n/a")
    return ToolResult(evidence=[mk_ev(
        counter, rid, "macro", value,
        f"yfinance (INR=X, BZ=F); FRED DGS10; RBI/MoSPI manual files (policy file to {pol_date})", as_ev,
        summary=", ".join(parts) or "macro data unavailable", confidence=round(0.8 - 0.1 * n_missing, 2),
        degraded=n_missing >= 3, reason="api_timeout" if n_missing >= 3 else None, t0=t0,
        source_url="https://fred.stlouisfed.org/series/DGS10")], warnings=warns + w2)


def store_cfg():
    from .util import CFG
    return CFG


# ------------------------------------------------------------------ weather
def _resolve_region(req) -> dict:
    if req.region_id:
        reg = next((r for r in load_json("regions.json") if r["region_id"] == req.region_id), None)
        if not reg:
            raise HTTPException(422, f"unknown region_id {req.region_id}")
        return reg
    if req.lat is not None and req.lon is not None:
        return {"region_id": None, "lat": req.lat, "lon": req.lon, "country": "IN"}
    raise HTTPException(422, "region_id or lat/lon required")


async def weather_handler(req, rid, counter, t0, chaos) -> ToolResult:
    reg = _resolve_region(req)
    lat, lon, key = reg["lat"], reg["lon"], (reg["region_id"] or f"{reg['lat']:.2f},{reg['lon']:.2f}")
    as_of, h = parse_as_of(req.as_of), max(1, min(req.horizon_days, 7))
    if "weather_down" in chaos:
        return ToolResult(evidence=[degraded_ev(
            counter, rid, "weather", key, "Open-Meteo", "chaos",
            "Open-Meteo unavailable; using cached value", {"region": key, "alerts": []}, t0=t0, as_of=as_of)],
            warnings=["weather_down chaos flag set"])

    today = (utcnow() + timedelta(hours=5, minutes=30)).date()
    lookahead, src, url = False, "Open-Meteo forecast + ERA5 climatology", openmeteo.FORECAST
    warns: list[str] = []

    if as_of is None:
        js = await cg("om_forecast", {"lat": lat, "lon": lon, "days": 7}, lambda: openmeteo.forecast(lat, lon, 7), ttl=1800)
        kind, start = "forecast", today
    else:
        start = as_of.date()
        s, e = start.isoformat(), (start + timedelta(days=h - 1)).isoformat()
        js, kind = None, "archive"
        if start.year >= 2022:  # archived model forecasts -> no look-ahead
            try:
                js = await cg("om_histfc", {"lat": lat, "lon": lon, "s": s, "e": e},
                              lambda: openmeteo.historical_forecast(lat, lon, s, e))
                kind, url, src = "historical_forecast", openmeteo.HISTFC, "Open-Meteo historical forecast + ERA5 climatology"
            except CacheMiss:
                raise
            except Exception:
                js = None
        if js is None:
            js = await cg("om_archive", {"lat": lat, "lon": lon, "s": s, "e": e}, lambda: openmeteo.archive(lat, lon, s, e))
            lookahead, url, src = True, openmeteo.ARCHIVE, "Open-Meteo ERA5 archive (look-ahead risk) + ERA5 climatology"
            warns.append("as_of weather uses ERA5 reanalysis: lookahead_risk=true")

    daily = openmeteo.parse_daily(js)[:h]
    if not daily:
        raise RuntimeError("empty daily forecast")
    fc_mm = sum(d["precip_mm"] or 0 for d in daily)

    y1 = 2025 if as_of is None else min(2025, as_of.year - 1)
    clim = await cg("om_clim", {"lat": lat, "lon": lon, "y0": 2001, "y1": 2025},
                    lambda: openmeteo.climatology(lat, lon, 2001, 2025))
    precip = pd.Series(clim["precip"], index=pd.to_datetime(clim["dates"]), dtype=float)
    clim_mm = window_clim_mm(precip, start, len(daily), years=range(2001, y1 + 1))
    anomaly = rain_anomaly_pct(fc_mm, clim_mm) if clim_mm is not None else None
    if anomaly is None:
        warns.append("climatology window unavailable: rain_anomaly_pct=null")

    soil = openmeteo.soil_day0(js, kind)
    z = None
    if soil is not None and clim.get("soil"):
        ss = pd.Series(clim["soil"], index=pd.to_datetime(clim["soil_dates"]), dtype=float)
        ss = ss[ss.index.year <= y1]
        mean, std = soil_clim_stats(ss, pd.Timestamp(start).dayofyear)
        z = round(soil_moisture_z(soil, mean, std), 2) if mean is not None else None

    hot = max(daily, key=lambda d: d["tmax_c"] if d["tmax_c"] is not None else -99)
    tmax = hot["tmax_c"] if hot["tmax_c"] is not None else 30.0
    hi = heat_index_c(tmax, hot["rh_pct"] if hot["rh_pct"] is not None else 60.0)

    storms: list[dict] = []
    if as_of is None:
        try:
            storms += await cg("nhc", {}, storm_src.nhc_current, ttl=900)
        except Exception:
            pass
    storms += storm_src.manual_storms(as_of)
    storm = storm_src.attach_storm(reg, storms)

    alerts = weather_alerts(anomaly, max((d["precip_mm"] or 0) for d in daily), tmax, hi, storm,
                            start.month, reg.get("country", "IN"))
    conf = weather_confidence(True, h, lookahead)
    value = {"region": key, "lat": lat, "lon": lon, "rain_anomaly_pct": anomaly, "heat_index_c": hi,
             "max_temp_c": tmax, "soil_moisture_0_7cm": soil, "soil_moisture_z": z, "storm": storm,
             "alerts": alerts, "horizon_days": h,
             "daily": [{"date": d["date"], "precip_mm": d["precip_mm"], "tmax_c": d["tmax_c"]} for d in daily],
             "lookahead_risk": lookahead}
    name = reg.get("name", key).split(" (")[0]
    summ = f"{name}: {h}-day rain {anomaly:+.0f}% vs 2001-{y1} normal" if anomaly is not None else f"{name}: {h}-day forecast"
    if storm:
        summ += f"; {storm['category']} {storm['name']} {storm['distance_km']} km away"
    synthetic = bool(storm and storm.get("synthetic"))
    if synthetic:
        value["synthetic"] = True
        summ = f"SIMULATED: {summ}"
        src += "; SIMULATED demo storm (DEMO_MODE)"
    elif storm and storm["basin"] == "NIO":
        src += "; IMD RSMC bulletin (manual)"
    as_ev = parse_dt(f"{daily[0]['date']}T00:00:00+05:30") if as_of else utcnow().replace(minute=0, second=0, microsecond=0)
    if as_of is None and not synthetic:  # only real live values are "last good"; never a backtest or demo value
        store.save_last_good("weather", key, value, as_ev, conf)
    ev = mk_ev(counter, rid, "weather", value, src, as_ev, summary=summ, confidence=conf, source_url=url, t0=t0)
    ev.synthetic = synthetic
    return ToolResult(evidence=[ev], warnings=warns)
