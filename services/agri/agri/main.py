"""Agri crop-stress service on L2:8103 (docs/09). Tool name "agri". Run from services/agri:
    ..\\..\\.venv\\Scripts\\python -m uvicorn agri.main:app --host 0.0.0.0 --port 8103

POST /agri_signal        {region_id, date|on_date?, crop_season?, as_of?, run_id?, chaos?}  -> ToolResult (1 Evidence)
POST /agri_signal/batch  {region_ids?|tickers?, date?, as_of?, crop_season?, run_id?, chaos?} -> ToolResult (1 per region)
GET  /regions            region metadata + latest composite + linked equities
GET  /model_info         metadata.json + LOYO CV (incl. persistence comparison) + per-fold report
GET  /health             copilot_common Health
Never raises to the caller: unknown region / no imagery / internal error -> 200 with a degraded Evidence.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import os
import threading
import time
from contextlib import asynccontextmanager
from typing import Any

import pandas as pd
from fastapi import Header, HTTPException
from pydantic import BaseModel, ConfigDict

from copilot_common.ids import EvidenceCounter, counter_for
from copilot_common.models import Evidence, ToolResult
from copilot_common.service_base import create_service_app, degraded, get_run_id, mock_or, now_utc
from copilot_common.settings import get_data_dir, get_settings

from . import __version__
from .exposure import linked_equities, regions_for_tickers
from .features import FeatureStore, model_row
from .predict import AgriModel, FeatureMismatchError
from .regions import get_regions

TOOL = "agri"
SOURCE_URL = "https://lpdaac.usgs.gov/products/mod13q1v061/"
DATA_SOURCE = "MODIS MOD13Q1 v061 NDVI/EVI 250m 16-day (district zonal mean, GEE export)"
WEATHER_SOURCE = "Open-Meteo ERA5 daily weather at the district centroid"
LABEL_SOURCE = "rule-based stress_class (VCI + rain deficit, 09 §5); no yield labels"
STALE_DAYS = int(os.getenv("AGRI_STALE_DAYS", "40"))       # > ~2.5 composites behind the requested date = stale
VPF_FULL = 0.7                                            # §1 confidence: min(1, valid_pixel_frac / 0.7)


def data_agri():
    return get_data_dir() / "agri"


def modis_csv():
    return os.getenv("AGRI_MODIS_CSV") or str(data_agri() / "modis" / "agri_modis_zonal_2011_2025.csv")


def weather_csv():
    return os.getenv("AGRI_WEATHER_CSV") or str(data_agri() / "weather" / "agri_weather_2011_2025_ALL.csv")


# ---------------------------------------------------------------- engine (lazy, thread-safe)
_lock = threading.Lock()
_state: dict[str, Any] = {"model": None, "store": None, "error": None}


def get_engine() -> tuple[AgriModel, FeatureStore]:
    """Load model + feature store once. FeatureMismatchError propagates (refuse to start, 09 §12)."""
    if _state["model"] is not None and _state["store"] is not None:
        return _state["model"], _state["store"]
    with _lock:
        if _state["model"] is None:
            _state["model"] = AgriModel()
        if _state["store"] is None:
            _state["store"] = FeatureStore(modis_csv(), weather_csv())
        _state["error"] = None
    return _state["model"], _state["store"]


@asynccontextmanager
async def lifespan(app):
    if not get_settings().MOCK:
        try:
            await asyncio.to_thread(get_engine)
        except FeatureMismatchError:
            raise                                        # refuse to start with a clear error
        except Exception as e:  # noqa: BLE001  data missing etc.: start, report on /health, degrade requests
            _state["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    yield


async def deps_check() -> dict[str, str]:
    if get_settings().MOCK:
        return {"model": "ok (mock)"}
    if _state["error"]:
        return {"model": f"load failed: {_state['error']}"}
    if _state["model"] is None:
        return {"model": "not loaded yet"}
    store: FeatureStore = _state["store"]
    age = (pd.Timestamp(now_utc().date()) - store.latest_period_end).days
    return {"model": "ok", "feature_store": "ok",
            "imagery": "ok" if age <= STALE_DAYS else
            f"stale: latest MODIS composite {store.latest_period_end.date()} ({age} d old)"}


app = create_service_app(TOOL, version=__version__, deps_check=deps_check,
                         models=[os.getenv("AGRI_MODEL_VERSION", "gbm_v2")], lifespan=lifespan)


# ---------------------------------------------------------------- requests
class AgriReq(BaseModel):
    model_config = ConfigDict(extra="ignore")
    region_id: str
    date: str | None = None            # orchestrator / monitor send "date"
    on_date: str | None = None         # alias
    crop_season: str | None = None
    as_of: str | None = None
    run_id: str | None = None
    chaos: dict | None = None


class BatchReq(BaseModel):
    model_config = ConfigDict(extra="ignore")
    region_ids: list[str] | None = None
    tickers: list[str] | None = None
    date: str | None = None
    on_date: str | None = None
    crop_season: str | None = None
    as_of: str | None = None
    run_id: str | None = None
    chaos: dict | None = None


def _day(s: Any, field: str) -> pd.Timestamp | None:
    if s is None or s == "":
        return None
    try:
        return pd.Timestamp(dt.date.fromisoformat(str(s)[:10]))
    except ValueError:
        raise HTTPException(422, f"{field} must be an ISO date (YYYY-MM-DD), got {s!r}")


def _f(v: Any, nd: int = 4) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else round(x, nd)


def _chaos_flag(chaos: dict | None, x_chaos: str | None) -> bool:
    hdr = {c.strip() for c in (x_chaos or "").split(",") if c.strip()}
    return bool((chaos or {}).get("agri_raster_missing")) or "agri_raster_missing" in hdr


def _canonical(region_id: str, known: list[str]) -> str | None:
    m = {r.lower(): r for r in known}
    return m.get((region_id or "").strip().lower())


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


# ---------------------------------------------------------------- core
def _signal(req: AgriReq, ev_id: str, run_id: str | None, raster_missing: bool) -> tuple[Evidence, list[str]]:
    t0 = time.perf_counter()
    warnings: list[str] = []
    try:
        model, store = get_engine()
    except Exception as e:  # noqa: BLE001
        ev = degraded(TOOL, ev_id, "model_unavailable", value={"region_id": req.region_id, "stress_class": None,
                      "yield_anomaly_pct": None}, source=f"agri service ({type(e).__name__})", confidence=0.1,
                      run_id=run_id)
        return ev, [f"agri model/data not loaded: {e}"]

    region = _canonical(req.region_id, store.regions)
    if region is None:
        known = store.regions
        ev = degraded(TOOL, ev_id, "unknown_region", run_id=run_id, confidence=0.1, source="agri service",
                      value={"region_id": req.region_id, "known_regions": known, "stress_class": None,
                             "class_probs": None, "yield_anomaly_pct": None,
                             "linked_equities": linked_equities(req.region_id), "model_version": model.version})
        ev.summary = f"agri: no model for region {req.region_id!r}"
        ev.latency_ms = _ms(t0)
        return ev, [f"unknown region {req.region_id!r}; the agri model knows: {', '.join(known)}"]

    today = pd.Timestamp(now_utc().date())
    on = _day(req.date or req.on_date, "date") or today
    as_of = _day(req.as_of, "as_of")
    cutoff = min(on, as_of) if as_of is not None else on
    if cutoff > today:
        warnings.append(f"date {cutoff.date()} is in the future; using data up to today")
        cutoff = today
    season = (req.crop_season or "").lower() or None
    if season not in (None, "kharif", "rabi"):
        warnings.append(f"crop_season {req.crop_season!r} not modelled (kharif|rabi); ignored")
        season = None

    reasons: list[str] = []
    offset = 1 if raster_missing else 0
    if raster_missing:
        reasons.append("raster_missing_last_cached")
        warnings.append("agri_raster_missing: newest composite treated as missing; using the previous (cached) one")
    row = store.row_at(region, cutoff, as_of, season, offset)
    if row is None and season:
        warnings.append(f"no {season} composite on or before {cutoff.date()}; using the latest of any season")
        row = store.row_at(region, cutoff, as_of, None, offset)
    if row is None:
        ev = degraded(TOOL, ev_id, "no_imagery_before_cutoff", run_id=run_id, confidence=0.1,
                      source=f"agri {model.version} on {DATA_SOURCE}",
                      value={"region_id": region, "stress_class": None, "class_probs": None,
                             "yield_anomaly_pct": None, "linked_equities": linked_equities(region),
                             "model_version": model.version,
                             "first_composite": str(store.first_period_end.date())})
        ev.latency_ms = _ms(t0)
        return ev, [f"no MODIS composite for {region} on or before {cutoff.date()} "
                    f"(archive starts {store.first_period_end.date()})"]

    meta = get_regions(weather_csv()).get(region, {})
    class_to_id = {c: i for i, c in enumerate(model.classes)}
    feats = model_row(row, class_to_id)
    missing = model.missing(feats)
    probs = model.predict(feats)
    stress_class = max(probs, key=probs.get)
    p_max = probs[stress_class]

    period_end: pd.Timestamp = row["period_end"]
    age_days = int((cutoff - period_end).days)
    if age_days > STALE_DAYS:
        reasons.append("stale_imagery")
        warnings.append(f"stale imagery: latest usable MODIS composite ends {period_end.date()}, {age_days} days "
                        f"before {cutoff.date()} (threshold {STALE_DAYS} d)")
    proxy = feats.get("NDVI_mean") is None or pd.isna(feats.get("NDVI_mean"))
    if proxy:
        reasons.append("proxy_weather_only")
        warnings.append("NDVI missing for this composite (cloud / no valid pixels): weather-only proxy prediction")
    if missing:
        warnings.append(f"missing model features {missing} (LightGBM treats them as missing; confidence x0.7)")
    nyb = int(row.get("n_years_baseline") or 0)
    if as_of is not None and nyb < 5:
        warnings.append(f"only {nyb} baseline year(s) on or before as_of {as_of.date()}; anomalies are noisy")
    by_min, by_max = row.get("baseline_year_min"), row.get("baseline_year_max")
    if as_of is None and by_max is not None and int(by_max) > int(row["season_year"]) and cutoff < today:
        warnings.append(f"baselines use other years up to {by_max} (leave-current-year-out, as in training); "
                        "pass as_of for a strict no-lookahead time machine")

    vpf = _f(row.get("valid_pixel_frac"))
    is_degraded = bool(reasons)
    conf = p_max * min(1.0, (vpf if vpf is not None else 1.0) / VPF_FULL) * (0.7 if missing else 1.0)
    conf *= model.skill_factor
    if is_degraded:
        conf *= 0.5
    if proxy:
        conf = min(conf, 0.3)
    conf = round(max(0.0, min(1.0, conf)), 3)

    prev = row.get("prev")
    z, z_prev = _f(row.get("ndvi_anomaly_z"), 3), _f(prev.get("ndvi_anomaly_z"), 3) if prev else None
    if z is None or z_prev is None:
        direction = "unknown"
    else:
        direction = "improving" if z - z_prev > 0.25 else "worsening" if z - z_prev < -0.25 else "stable"

    value = {
        "region_id": region, "district": meta.get("district"), "state": meta.get("state"),
        "crop_season": row["crop_season"], "season_year": int(row["season_year"]),
        "main_crops": meta.get("main_crops", []),
        "lat": meta.get("lat"), "lon": meta.get("lon"),
        "period_start": str(row["image_date"].date()), "period_end": str(period_end.date()),
        "features": {
            "ndvi_mean": _f(row.get("NDVI_mean")), "ndvi_max": _f(row.get("NDVI_max")),
            "ndvi_std": _f(row.get("NDVI_stdDev")), "ndvi_anomaly_z": z, "vci": _f(row.get("vci"), 3),
            "ndvi_delta": _f(row.get("ndvi_delta")), "evi_mean": _f(row.get("EVI_mean")),
            "rain_30d_mm": _f(row.get("rain_30d_mm"), 1), "rain_anomaly_pct": _f(row.get("rain_anomaly_pct"), 1),
            "soil_moisture_0_7cm": _f(row.get("soil_moisture_0_7cm")),
            "soil_moisture_anomaly_z": _f(row.get("soil_moisture_anomaly_z"), 3),
            "lst_anomaly_c": None, "valid_pixel_frac": vpf, "doy_bin": int(row["doy_bin"]),
        },
        "stress_class": stress_class,                               # model forecast for the NEXT ~16 days
        "stress_class_now": row.get("stress_class_now") if isinstance(row.get("stress_class_now"), str) else None,
        "class_probs": probs,
        "stress_lead_days": int(model.metadata.get("target_lead_periods", 1) * model.metadata.get("period_days", 16)),
        "yield_anomaly_pct": None,
        "trend": {"ndvi_anomaly_z_prev": z_prev, "direction": direction,
                  "prev_period_end": str(prev["period_end"].date()) if prev else None,
                  "stress_class_now_prev": prev.get("stress_class_now") if prev and isinstance(
                      prev.get("stress_class_now"), str) else None},
        "linked_equities": linked_equities(region),
        "baseline_years": f"{by_min}-{by_max}" if by_min is not None else None,
        "n_years_baseline": nyb,
        "imagery_age_days": age_days,
        "model_version": model.version, "data_source": DATA_SOURCE, "weather_source": WEATHER_SOURCE,
        "label_source": LABEL_SOURCE,
        "model_skill": {"beats_persistence": model.beats_persistence,
                        "cv_accuracy": _f(model.cv.get("model_accuracy"), 3),
                        "cv_macro_f1": _f(model.cv.get("model_macro_f1"), 3),
                        "persistence_accuracy": _f(model.cv.get("persistence_accuracy"), 3),
                        "persistence_macro_f1": _f(model.cv.get("persistence_macro_f1"), 3),
                        "confidence_factor": model.skill_factor},
        "degraded": is_degraded,
        "degraded_reasons": reasons,
    }
    warnings.append("yield_anomaly_pct is null: no yield model (no district yield labels were available)")

    zt = f"{z:+.2f}σ" if z is not None else "n/a"
    vt = f"{value['features']['vci']:.2f}" if value["features"]["vci"] is not None else "n/a"
    rt = f"{value['features']['rain_anomaly_pct']:+.0f}%" if value["features"]["rain_anomaly_pct"] is not None else "n/a"
    summary = (f"{meta.get('district', region)} {row['crop_season']} {int(row['season_year'])} "
               f"(MODIS composite to {period_end.date()}, {age_days} d old): NDVI {zt} vs {value['baseline_years']}, "
               f"VCI {vt}, season rain {rt} -> next ~{value['stress_lead_days']} d: {stress_class} (p={p_max:.2f}); "
               f"no yield model" + ("; STALE imagery" if "stale_imagery" in reasons else ""))

    as_of_dt = dt.datetime(period_end.year, period_end.month, period_end.day, tzinfo=dt.timezone.utc)
    ts = now_utc()
    ev = Evidence(id=ev_id, run_id=run_id, tool=TOOL, value=value, summary=summary,
                  source=f"agri {model.version} on MODIS MOD13Q1 NDVI + Open-Meteo ERA5 weather",
                  source_url=SOURCE_URL, as_of=as_of_dt, timestamp=ts,
                  freshness_s=max(0, int((ts - as_of_dt).total_seconds())), confidence=conf,
                  degraded=is_degraded, degraded_reason="+".join(reasons) if reasons else None,
                  latency_ms=_ms(t0), model_version=model.version)
    return ev, warnings


def _dedupe(ws: list[str]) -> list[str]:
    return list(dict.fromkeys(ws))


# ---------------------------------------------------------------- endpoints
@app.post("/agri_signal")
async def agri_signal(req: AgriReq, x_chaos: str | None = Header(None)):
    run_id = get_run_id() or req.run_id
    return await mock_or(TOOL, "/agri_signal", lambda: _real_signal(req, run_id, x_chaos))


async def _real_signal(req: AgriReq, run_id: str | None, x_chaos: str | None) -> dict:
    ev_id = counter_for(run_id).next(TOOL) if run_id else "ev_agri_001"
    ev, warnings = await asyncio.to_thread(_signal, req, ev_id, run_id, _chaos_flag(req.chaos, x_chaos))
    return ToolResult(evidence=[ev], warnings=_dedupe(warnings)).model_dump(mode="json")


@app.post("/agri_signal/batch")
async def agri_signal_batch(req: BatchReq, x_chaos: str | None = Header(None)):
    run_id = get_run_id() or req.run_id
    return await mock_or(TOOL, "/agri_signal/batch", lambda: _real_batch(req, run_id, x_chaos))


async def _real_batch(req: BatchReq, run_id: str | None, x_chaos: str | None) -> dict:
    warnings: list[str] = []
    try:
        _, store = await asyncio.to_thread(get_engine)
        known = store.regions
    except Exception as e:  # noqa: BLE001
        known = []
        warnings.append(f"agri model/data not loaded: {e}")
    if req.region_ids:
        regions = list(dict.fromkeys(req.region_ids))
    elif req.tickers:
        linked = regions_for_tickers(req.tickers)
        regions = []
        for rid, tks in linked.items():
            canon = _canonical(rid, known)
            if canon:
                regions.append(canon)
            else:
                warnings.append(f"{', '.join(tks)} linked to {rid}: no agri model for that region (skipped)")
        unlinked = [t for t in req.tickers if not any(t in v for v in linked.values())]
        if unlinked:
            warnings.append(f"no agri region linked to: {', '.join(unlinked)}")
    else:
        regions = list(known)
    counter = counter_for(run_id) if run_id else EvidenceCounter()
    evs = []
    flag = _chaos_flag(req.chaos, x_chaos)
    for rid in regions:
        one = AgriReq(region_id=rid, date=req.date, on_date=req.on_date, crop_season=req.crop_season,
                      as_of=req.as_of, run_id=run_id)
        ev, w = await asyncio.to_thread(_signal, one, counter.next(TOOL), run_id, flag)
        evs.append(ev)
        warnings.extend(f"{rid}: {x}" if not x.startswith(rid) else x for x in w)
    return ToolResult(evidence=evs, warnings=_dedupe(warnings)).model_dump(mode="json")


@app.get("/regions")
async def regions():
    try:
        _, store = await asyncio.to_thread(get_engine)
    except Exception as e:  # noqa: BLE001
        return {"regions": [], "error": f"{type(e).__name__}: {e}"}
    meta = get_regions(weather_csv())
    out = []
    for rid in store.regions:
        latest = store.latest(rid)
        out.append({**meta.get(rid, {"region_id": rid}), "in_model": True,
                    "latest_period_end": str(latest.date()) if latest is not None else None,
                    "n_composites": int((store.history["region_id"] == rid).sum()),
                    "linked_equities": linked_equities(rid)})
    return {"regions": out, "data_source": DATA_SOURCE, "weather_source": WEATHER_SOURCE,
            "stale_after_days": STALE_DAYS}


@app.get("/model_info")
async def model_info():
    try:
        model, store = await asyncio.to_thread(get_engine)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    md = model.metadata
    cv = md.get("cv", {})
    folds = []
    rep = model.path / "cv_report.json"
    if rep.is_file():
        folds = json.loads(rep.read_text(encoding="utf-8")).get("folds", [])
    verdict = ("beats persistence" if model.beats_persistence else
               f"does NOT beat persistence: LOYO accuracy {cv.get('model_accuracy', 0):.3f} vs "
               f"{cv.get('persistence_accuracy', 0):.3f}, macro-F1 {cv.get('model_macro_f1', 0):.3f} vs "
               f"{cv.get('persistence_macro_f1', 0):.3f}; confidence is multiplied by {model.skill_factor}")
    return {**md, "verdict": verdict, "confidence_skill_factor": model.skill_factor, "cv_folds": folds,
            "yield_model": None, "stale_after_days": STALE_DAYS,
            "imagery_range": [str(store.first_period_end.date()), str(store.latest_period_end.date())],
            "confidence_formula": "max(class_probs) x min(1, valid_pixel_frac/0.7) x (0.7 if any model feature "
                                  "missing) x skill_factor x (0.5 if degraded); capped 0.3 in proxy mode"}
