"""Append new MODIS MOD13Q1 NDVI/EVI composites to data/agri/modis/agri_modis_zonal_2011_2025.csv via NASA AppEEARS.

    .venv/Scripts/python services/agri/scripts/refresh_modis.py            # fetch, check bias, append
    .venv/Scripts/python services/agri/scripts/refresh_modis.py --rebuild  # ... then rebuild features and retrain

Needs a free NASA Earthdata login in the root .env:  EARTHDATA_USERNAME=...  EARTHDATA_PASSWORD=...
Without them the script stops before any download and says what is missing.

How it matches the existing table (an Earth Engine export over FAO GAUL level-2 districts):
  * district polygons come from geoBoundaries (IND ADM2, gbOpen; source lgdirectory.gov.in, 2021). They differ a little
    from GAUL, so the script also requests the last OVERLAP_COMPOSITES composites already in the table and reports the
    mean NDVI difference. If |bias| > BIAS_LIMIT it stops without appending (fix or accept with --accept-bias).
  * AppEEARS area statistics give Mean / Maximum / Standard Deviation / Count per polygon, date and layer, i.e. the
    same NDVI_mean, NDVI_max, NDVI_stdDev, NDVI_count (and EVI_*) columns. image_date = composite start date;
    period_end = image_date + 15 days (16-day composite), as in the existing table.
Only composites newer than the table's last image_date are appended. Nothing is filled in when a district is missing.
UNTESTED against the live AppEEARS service (no credentials were available when this was written); the boundary
selection and the statistics conversion are unit-tested with recorded examples.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
MODIS_CSV = ROOT / "data" / "agri" / "modis" / "agri_modis_zonal_2011_2025.csv"
BOUNDARY_CACHE = ROOT / "data" / "cache" / "agri" / "geoBoundaries-IND-ADM2_simplified.geojson"
BOUNDARY_URL = ("https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/IND/ADM2/"
                "geoBoundaries-IND-ADM2_simplified.geojson")
APPEEARS = "https://appeears.earthdatacloud.nasa.gov/api"
PRODUCT = "MOD13Q1.061"
LAYERS = {"_250m_16_days_NDVI": "NDVI", "_250m_16_days_EVI": "EVI"}
# (ADM1_NAME, ADM2_NAME) exactly as in the existing table -> geoBoundaries shapeName
DISTRICTS = {("Gujarat", "Rajkot"): "Rajkot", ("Karnataka", "Gulbarga"): "Gulbarga",
             ("Madhya Pradesh", "Indore"): "Indore", ("Madhya Pradesh", "Ujjain"): "Ujjain",
             ("Maharashtra", "Latur"): "Latur", ("Maharashtra", "Yavatmal"): "Yavatmal",
             ("Punjab", "Ludhiana"): "Ludhiana", ("Rajasthan", "Jodhpur"): "Jodhpur"}
OVERLAP_COMPOSITES = 4
BIAS_LIMIT = 0.03          # NDVI units


def load_env() -> tuple[str, str]:
    from dotenv import dotenv_values
    env = {**dotenv_values(ROOT / ".env"), **os.environ}
    return (env.get("EARTHDATA_USERNAME") or "").strip(), (env.get("EARTHDATA_PASSWORD") or "").strip()


def district_features(geojson: dict) -> dict:
    """FeatureCollection with one feature per model district; properties carry the table's ADM1/ADM2 names."""
    by_name = {f["properties"]["shapeName"]: f for f in geojson["features"]}
    feats, missing = [], []
    for (adm1, adm2), shape in DISTRICTS.items():
        f = by_name.get(shape)
        if f is None:
            missing.append(shape)
            continue
        feats.append({"type": "Feature", "geometry": f["geometry"], "properties": {"ADM1_NAME": adm1, "ADM2_NAME": adm2}})
    if missing:
        raise ValueError(f"districts not found in geoBoundaries: {missing}")
    return {"type": "FeatureCollection", "features": feats}


def stats_to_table(stats: pd.DataFrame, features: dict) -> pd.DataFrame:
    """AppEEARS area '*-Statistics.csv' rows -> the table's columns. aid i = the i-th submitted feature (1-based)."""
    names = {i + 1: (f["properties"]["ADM1_NAME"], f["properties"]["ADM2_NAME"]) for i, f in enumerate(features["features"])}
    stats = stats.copy()
    aid = stats["aid"].astype(str).str.extract(r"(\d+)")[0].astype(int)
    stats["ADM1_NAME"] = aid.map(lambda a: names[a][0])
    stats["ADM2_NAME"] = aid.map(lambda a: names[a][1])
    stats["layer"] = stats["Dataset"].map(lambda d: next((v for k, v in LAYERS.items() if k.strip("_") in str(d)), None))
    stats = stats.dropna(subset=["layer"])
    for col in ("Mean", "Maximum", "Standard Deviation"):
        stats[col] = pd.to_numeric(stats[col], errors="coerce")
    for layer in LAYERS.values():                                     # guard per layer: raw MODIS integers are 1e4x
        rows = stats["layer"] == layer
        if stats.loc[rows, "Mean"].abs().max() > 1.5:
            stats.loc[rows, ["Mean", "Maximum", "Standard Deviation"]] /= 10000
    stats["image_date"] = pd.to_datetime(stats["Date"]).dt.date
    wide = stats.pivot_table(index=["ADM1_NAME", "ADM2_NAME", "image_date"], columns="layer",
                             values=["Mean", "Maximum", "Standard Deviation", "Count"], aggfunc="first")
    out = pd.DataFrame(index=wide.index).reset_index()
    for layer in LAYERS.values():
        out[f"{layer}_mean"] = wide[("Mean", layer)].values
        out[f"{layer}_max"] = wide[("Maximum", layer)].values
        out[f"{layer}_stdDev"] = wide[("Standard Deviation", layer)].values
        out[f"{layer}_count"] = wide[("Count", layer)].values.astype(int)
    out["period_end"] = pd.to_datetime(out["image_date"]) + timedelta(days=15)
    out["image_date"] = pd.to_datetime(out["image_date"])
    cols = ["ADM1_NAME", "ADM2_NAME", "period_end", "image_date", "NDVI_mean", "NDVI_max", "NDVI_stdDev", "NDVI_count",
            "EVI_mean", "EVI_max", "EVI_stdDev", "EVI_count"]
    return out[cols].sort_values(["image_date", "ADM1_NAME", "ADM2_NAME"]).reset_index(drop=True)


def ndvi_bias(new: pd.DataFrame, old: pd.DataFrame) -> tuple[float | None, int]:
    key = ["ADM1_NAME", "ADM2_NAME", "image_date"]
    o = old.assign(image_date=pd.to_datetime(old["image_date"]))[key + ["NDVI_mean"]]
    m = new[key + ["NDVI_mean"]].merge(o, on=key, suffixes=("_new", "_old"))
    if m.empty:
        return None, 0
    return float((m["NDVI_mean_new"] - m["NDVI_mean_old"]).mean()), len(m)


def appeears_stats(user: str, pwd: str, features: dict, start: date, end: date) -> pd.DataFrame:
    with httpx.Client(timeout=120) as c:
        tok = c.post(f"{APPEEARS}/login", auth=(user, pwd)).raise_for_status().json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        task = {"task_type": "area", "task_name": f"nitr_modis_{start:%Y%m%d}_{end:%Y%m%d}",
                "params": {"dates": [{"startDate": start.strftime("%m-%d-%Y"), "endDate": end.strftime("%m-%d-%Y")}],
                           "layers": [{"product": PRODUCT, "layer": l} for l in LAYERS],
                           "output": {"format": {"type": "geotiff"}, "projection": "geographic"},
                           "geo": features}}
        tid = c.post(f"{APPEEARS}/task", json=task, headers=h).raise_for_status().json()["task_id"]
        print(f"AppEEARS task {tid} submitted; waiting (area tasks usually take 5-30 minutes)...")
        while True:
            st = c.get(f"{APPEEARS}/task/{tid}", headers=h).raise_for_status().json()["status"]
            if st == "done":
                break
            if st in ("error", "expired", "deleted"):
                raise RuntimeError(f"AppEEARS task {tid} ended with status {st}")
            time.sleep(30)
        files = c.get(f"{APPEEARS}/bundle/{tid}", headers=h).raise_for_status().json()["files"]
        stat = next(f for f in files if f["file_name"].endswith("Statistics.csv") and "MOD13Q1" in f["file_name"])
        csv = c.get(f"{APPEEARS}/bundle/{tid}/{stat['file_id']}", headers=h, follow_redirects=True).raise_for_status().text
        return pd.read_csv(io.StringIO(csv))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="after appending, rebuild features and retrain (gbm_v3)")
    ap.add_argument("--accept-bias", action="store_true", help="append even if |NDVI bias| > %.2f" % BIAS_LIMIT)
    args = ap.parse_args()
    user, pwd = load_env()
    if not user or not pwd:
        print("NEEDS HUMAN INPUT: set EARTHDATA_USERNAME and EARTHDATA_PASSWORD in the root .env "
              "(free account at https://urs.earthdata.nasa.gov). Nothing was downloaded.")
        return 2
    if not BOUNDARY_CACHE.exists():
        BOUNDARY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        BOUNDARY_CACHE.write_bytes(httpx.get(BOUNDARY_URL, timeout=180, follow_redirects=True).raise_for_status().content)
    features = district_features(json.loads(BOUNDARY_CACHE.read_text(encoding="utf-8")))
    old = pd.read_csv(MODIS_CSV)
    dates = sorted(pd.to_datetime(old["image_date"]).unique())
    start = pd.Timestamp(dates[-OVERLAP_COMPOSITES]).date()                 # overlap for the bias check
    new = stats_to_table(appeears_stats(user, pwd, features, start, date.today()), features)
    bias, n = ndvi_bias(new, old)
    print(f"NDVI bias vs existing table on {n} overlapping district-composites: {bias if bias is None else round(bias, 4)}")
    if bias is None or (abs(bias) > BIAS_LIMIT and not args.accept_bias):
        print("Stopping without appending: overlap check failed. Re-run with --accept-bias after reviewing.")
        return 3
    fresh = new[new["image_date"] > pd.to_datetime(old["image_date"]).max()]
    if fresh.empty:
        print("No newer composites available yet.")
        return 0
    out = pd.concat([old.assign(period_end=pd.to_datetime(old["period_end"]), image_date=pd.to_datetime(old["image_date"])),
                     fresh], ignore_index=True)
    out.to_csv(MODIS_CSV, index=False, date_format="%Y-%m-%d")
    print(f"Appended {len(fresh)} rows; newest composite now ends {fresh['period_end'].max().date()}.")
    if args.rebuild:
        train = ROOT / "services" / "agri" / "training"
        py = sys.executable
        subprocess.run([py, "build_table.py"], cwd=train, check=True)
        subprocess.run([py, "train_model.py", "--version", "gbm_v3"], cwd=train, check=True)
        print("Retrained as gbm_v3; compare models/gbm_v3/cv_report.json with gbm_v2 and update MODEL_CARD.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
