"""STEP 3 - build the training table (fixed copy of agri/03_build_table.py).

Inputs  (data/agri/):  modis/agri_modis_zonal_2011_2025.csv, weather/agri_weather_2011_2025_ALL.csv
Outputs (data/agri/):  agri_training.csv, baselines.parquet, latest_features.parquet

Changes vs the teammate's script (logic otherwise identical, see agri/features.py docstring):
  1. ndvi_delta is season-safe (NaN when the previous composite is in another crop season / season year or > 24 d
     earlier). The original diffed across the Apr-May gap and kharif->rabi (03_build_table.py:510-514), while its own
     CV used a season-safe delta -> train/serve skew.
  2. The feature recipe lives in agri/features.py and is shared with the service and the CV in train_model.py.
  3. valid_pixel_frac = NDVI_count / region max count (proxy; the GEE export has no total-pixel count).
  4. Paths resolve relative to this file, not the CWD.

Run:  .venv/Scripts/python services/agri/training/build_table.py [--compare agri/data/agri/agri_training.csv]
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from _paths import DATA_AGRI

from agri.features import (add_anomalies, add_targets, build_history, load_modis, load_weather_daily)

MODIS_FILE = DATA_AGRI / "modis" / "agri_modis_zonal_2011_2025.csv"
WEATHER_FILE = DATA_AGRI / "weather" / "agri_weather_2011_2025_ALL.csv"
TRAINING_FILE = DATA_AGRI / "agri_training.csv"
BASELINES_FILE = DATA_AGRI / "baselines.parquet"
LATEST_FILE = DATA_AGRI / "latest_features.parquet"

FINAL_COLUMNS = [
    "region_id", "ADM2_NAME", "ADM1_NAME", "crop_season", "season_year", "period_end", "image_date", "doy_bin",
    "NDVI_mean", "NDVI_max", "NDVI_stdDev", "EVI_mean", "EVI_max", "EVI_stdDev", "valid_pixel_frac",
    "ndvi_anomaly_z", "vci", "ndvi_delta", "rain_30d_mm", "rain_season_to_date_mm", "rain_normal_mm",
    "rain_anomaly_pct", "soil_moisture_0_7cm", "soil_moisture_anomaly_z", "temperature_2m_mean",
    "temperature_2m_max", "temperature_2m_min", "stress_class_now", "target_stress_class", "lst_anomaly_c",
]


def build() -> pd.DataFrame:
    for f in (MODIS_FILE, WEATHER_FILE):
        if not f.exists():
            raise FileNotFoundError(f"Required file not found: {f}")
    modis = load_modis(MODIS_FILE)
    daily = load_weather_daily(WEATHER_FILE)
    if set(modis["region_id"]) != set(daily["region_id"]):
        raise ValueError(f"region mismatch MODIS {sorted(set(modis.region_id))} vs weather {sorted(set(daily.region_id))}")
    hist = build_history(modis, daily)
    df = add_anomalies(hist, hist)                      # leave-current-year-out over all years
    df = add_targets(df)
    df["lst_anomaly_c"] = np.nan                        # LST not downloaded (optional in the spec)
    return df[FINAL_COLUMNS].sort_values(["region_id", "period_end"]).reset_index(drop=True)


def baselines_of(df: pd.DataFrame) -> pd.DataFrame:
    """All-years baselines per (region_id, doy_bin) - the 09 §7 artifact (the service recomputes leave-year-out
    baselines on the fly; this file is kept for the contract and for the UI)."""
    return df.groupby(["region_id", "doy_bin"], as_index=False).agg(
        ndvi_mu=("NDVI_mean", "mean"), ndvi_sigma=("NDVI_mean", "std"), ndvi_min=("NDVI_mean", "min"),
        ndvi_max=("NDVI_mean", "max"), rain_normal_mm=("rain_season_to_date_mm", "mean"),
        sm_mu=("soil_moisture_0_7cm", "mean"), sm_sigma=("soil_moisture_0_7cm", "std"),
        n_years=("season_year", "nunique"))


def compare(df: pd.DataFrame, other_csv: str) -> None:
    old = pd.read_csv(other_csv, parse_dates=["period_end", "image_date"])
    m = df.merge(old, on=["region_id", "period_end"], suffixes=("", "_old"), validate="one_to_one")
    print(f"\nCompare with {other_csv}: rows new={len(df)} old={len(old)} matched={len(m)}")
    for c in ["ndvi_anomaly_z", "vci", "rain_30d_mm", "rain_season_to_date_mm", "rain_normal_mm", "rain_anomaly_pct",
              "soil_moisture_0_7cm", "soil_moisture_anomaly_z", "doy_bin", "ndvi_delta"]:
        a, b = m[c].astype(float), m[f"{c}_old"].astype(float)
        both_nan = a.isna() & b.isna()
        diff = ~both_nan & ~np.isclose(a, b, rtol=1e-9, atol=1e-9, equal_nan=True)
        print(f"  {c:26s} rows differing: {int(diff.sum()):4d}")
    for c in ["stress_class_now", "target_stress_class"]:
        diff = ~(m[c].fillna("NA") == m[f"{c}_old"].fillna("NA"))
        print(f"  {c:26s} rows differing: {int(diff.sum()):4d}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--compare", help="another agri_training.csv to diff against (e.g. the teammate's)")
    args = ap.parse_args(argv)
    df = build()
    TRAINING_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(TRAINING_FILE, index=False)
    baselines_of(df).to_parquet(BASELINES_FILE, index=False)
    df.sort_values("period_end").groupby("region_id").tail(1).reset_index(drop=True).to_parquet(LATEST_FILE, index=False)
    print(f"Rows: {len(df):,}  regions: {df.region_id.nunique()}  "
          f"{df.period_end.min().date()} -> {df.period_end.max().date()}")
    print("stress_class_now:", df["stress_class_now"].value_counts(dropna=False).to_dict())
    print("target:", df["target_stress_class"].value_counts(dropna=False).to_dict())
    print(f"Saved {TRAINING_FILE}\nSaved {BASELINES_FILE}\nSaved {LATEST_FILE}")
    if args.compare:
        compare(df, args.compare)


if __name__ == "__main__":
    sys.exit(main())
