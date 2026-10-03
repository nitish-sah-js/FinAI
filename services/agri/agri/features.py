"""Feature recipe shared by TRAINING (training/build_table.py, training/train_model.py) and SERVING (main.py).

One code path, so the model sees the same features at train and serve time.

Per MODIS composite (region_id, image_date, period_end = image_date + 15 d):
  crop_season   kharif (period_end month Jun-Oct) | rabi (Nov-Mar); Apr-May composites are dropped
  season_year   calendar year of period_end, +1 for Nov/Dec (rabi Nov-2025..Mar-2026 = 2026)
  doy_bin       clip(ceil(doy(period_end)/16), 1, 23)
  ndvi_delta    NDVI_mean - NDVI_mean(previous composite), NaN when the previous composite is in another
                (crop_season, season_year) or more than 24 days earlier  <- FIX vs agri/03_build_table.py:510-514,
                which diffed across the Apr-May gap and the kharif->rabi boundary
  weather       Open-Meteo daily, aggregated over [image_date, period_end]: rain_30d_mm and
                rain_season_to_date_mm = value on the last day <= period_end; soil_moisture_0_7cm = window mean
  anomalies     leave-current-year-out, per (region_id, doy_bin), on YEARLY means of the reference rows:
                ndvi_anomaly_z = (NDVI - mu)/sigma, vci = clip((NDVI - min)/(max - min), 0, 1),
                rain_anomaly_pct = 100 (rain_std - normal)/normal, soil_moisture_anomaly_z = (sm - mu)/sigma
  stress_class_now  rule of 09 §5 (severe / stressed / watch / healthy; NaN if vci or rain anomaly is NaN)

`reference` is everything for the training table, the training years inside a CV fold, and rows with
period_end <= as_of at serve time (time machine), so baselines never use data after as_of.
"""
from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from .regions import GAUL_TO_REGION

STRESS_CLASSES = ["healthy", "watch", "stressed", "severe"]      # rule label vocabulary (09 §5)
SEASON_GAP_DAYS = 24                                             # > 1.5 periods apart = not consecutive
PERIOD_DAYS = 16

NDVI_COLS = ["NDVI_mean", "NDVI_max", "NDVI_stdDev", "EVI_mean", "EVI_max", "EVI_stdDev"]
WEATHER_PERIOD_COLS = ["rain_30d_mm", "rain_season_to_date_mm", "soil_moisture_0_7cm",
                       "temperature_2m_mean", "temperature_2m_max", "temperature_2m_min"]
ANOMALY_COLS = ["ndvi_anomaly_z", "vci", "rain_normal_mm", "rain_anomaly_pct", "soil_moisture_anomaly_z",
                "stress_class_now", "n_years_baseline", "baseline_year_min", "baseline_year_max"]


# ---------------------------------------------------------------- seasons
def crop_season_of(month: pd.Series) -> np.ndarray:
    return np.select([month.between(6, 10), month.isin([11, 12, 1, 2, 3])], ["kharif", "rabi"], default="none")


def season_year_of(dates: pd.Series) -> pd.Series:
    y = dates.dt.year.astype(int)
    return y + dates.dt.month.isin([11, 12]).astype(int)


def doy_bin_of(dates: pd.Series) -> pd.Series:
    return np.ceil(dates.dt.dayofyear / PERIOD_DAYS).astype(int).clip(1, 23)


# ---------------------------------------------------------------- loaders
def load_modis(path: Path) -> pd.DataFrame:
    m = pd.read_csv(path)
    m["image_date"] = pd.to_datetime(m["image_date"])
    m["period_end"] = pd.to_datetime(m["period_end"])
    m["region_id"] = [GAUL_TO_REGION.get((s, d)) for s, d in zip(m["ADM1_NAME"], m["ADM2_NAME"])]
    if m["region_id"].isna().any():
        bad = m.loc[m["region_id"].isna(), ["ADM1_NAME", "ADM2_NAME"]].drop_duplicates()
        raise ValueError(f"Unknown MODIS regions:\n{bad.to_string(index=False)}")
    # The GEE export has NDVI_count but no total-pixel count. Proxy: count / the region's max count over the archive
    # (a cloud-free composite). Not a model feature; used only in the confidence formula.
    mx = m.groupby("region_id")["NDVI_count"].transform("max").replace(0, np.nan)
    m["valid_pixel_frac"] = (m["NDVI_count"] / mx).clip(0, 1)
    return m


def load_weather_daily(path: Path) -> pd.DataFrame:
    """Daily Open-Meteo rows + rain_30d_mm, crop_season, season_year, rain_season_to_date_mm (as agri/03)."""
    w = pd.read_csv(path, parse_dates=["date"]).rename(columns={"region": "region_id"})
    w = w.sort_values(["region_id", "date"]).reset_index(drop=True)
    parts = []
    for rid, g in w.groupby("region_id", sort=False):
        g = g.sort_values("date").reset_index(drop=True).copy()
        g["region_id"] = rid
        g["rain_30d_mm"] = g["precipitation_sum"].rolling(window=30, min_periods=30).sum()
        g["crop_season"] = crop_season_of(g["date"].dt.month)
        g["season_year"] = season_year_of(g["date"])
        g["rain_season_to_date_mm"] = np.nan
        for (_, season), idx in g.groupby(["season_year", "crop_season"]).groups.items():
            if season == "none":
                continue
            idx = list(idx)
            g.loc[idx, "rain_season_to_date_mm"] = g.loc[idx, "precipitation_sum"].fillna(0).cumsum().to_numpy()
        parts.append(g)
    return pd.concat(parts, ignore_index=True).sort_values(["region_id", "date"]).reset_index(drop=True)


def aggregate_weather(modis: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """One weather row per composite: window [image_date, period_end] of the daily table (only days <= period_end)."""
    lookup = {rid: g.sort_values("date").reset_index(drop=True) for rid, g in daily.groupby("region_id", sort=False)}
    rows = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for r in modis[["region_id", "image_date", "period_end"]].itertuples(index=False):
            base = {"region_id": r.region_id, "image_date": r.image_date, "period_end": r.period_end}
            g = lookup.get(r.region_id)
            if g is not None:
                d = g["date"].to_numpy()
                lo = np.searchsorted(d, np.datetime64(r.image_date), "left")
                hi = np.searchsorted(d, np.datetime64(r.period_end), "right")
                if hi > lo:
                    w = g.iloc[lo:hi]
                    base.update({
                        "temperature_2m_mean": w["temperature_2m_mean"].mean(),
                        "temperature_2m_max": w["temperature_2m_max"].max(),
                        "temperature_2m_min": w["temperature_2m_min"].min(),
                        "rain_30d_mm": w["rain_30d_mm"].iloc[-1],
                        "soil_moisture_0_7cm": w["soil_moisture_0_to_7cm_mean"].mean(),
                        "rain_season_to_date_mm": w["rain_season_to_date_mm"].iloc[-1],
                        "weather_last_date": w["date"].iloc[-1],
                    })
            rows.append(base)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- history table (no anomalies yet)
def season_safe_delta(df: pd.DataFrame) -> pd.Series:
    """NDVI_mean - previous composite's NDVI_mean, only when both are in the same crop season of the same season
    year and at most SEASON_GAP_DAYS apart. `df` must be sorted by (region_id, period_end)."""
    g = df.groupby("region_id")
    prev_ndvi = g["NDVI_mean"].shift(1)
    same = ((g["crop_season"].shift(1) == df["crop_season"])
            & (g["season_year"].shift(1) == df["season_year"])
            & ((df["period_end"] - g["period_end"].shift(1)).dt.days <= SEASON_GAP_DAYS))
    return (df["NDVI_mean"] - prev_ndvi).where(same)


def build_history(modis: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """All in-season composites with raw NDVI + weather + season keys + ndvi_delta (sorted region, period_end)."""
    wp = aggregate_weather(modis, daily)
    df = modis.merge(wp, on=["region_id", "image_date", "period_end"], how="left", validate="one_to_one")
    df["crop_season"] = crop_season_of(df["period_end"].dt.month)
    df["season_year"] = season_year_of(df["period_end"])
    df = df[df["crop_season"].isin(["kharif", "rabi"])].copy()
    df["doy_bin"] = doy_bin_of(df["period_end"])
    df = df.sort_values(["region_id", "period_end"]).reset_index(drop=True)
    df["ndvi_delta"] = season_safe_delta(df)
    return df


# ---------------------------------------------------------------- leave-current-year-out anomalies
def _yearly(reference: pd.DataFrame, col: str) -> dict[tuple, tuple[np.ndarray, np.ndarray]]:
    """(region_id, doy_bin) -> (season_years, yearly mean of col) over non-NaN reference rows."""
    y = (reference[["region_id", "doy_bin", "season_year", col]].dropna(subset=[col])
         .groupby(["region_id", "doy_bin", "season_year"], as_index=False)[col].mean())
    out = {}
    for key, g in y.groupby(["region_id", "doy_bin"], sort=False):
        out[key] = (g["season_year"].to_numpy(dtype=int), g[col].to_numpy(dtype=float))
    return out


def _others(arrays: dict, key: tuple, year: int) -> tuple[np.ndarray, np.ndarray]:
    yrs, vals = arrays.get(key, (np.array([], dtype=int), np.array([], dtype=float)))
    m = yrs != year
    return vals[m], yrs[m]


def stress_rule(vci: float, rain: float, ndvi_z: float) -> str | float:
    """09 §5, evaluated top-down (identical to agri/03_build_table.py stress_label)."""
    if pd.isna(vci) or pd.isna(rain):
        return np.nan
    if vci < 0.20 and rain <= -30:
        return "severe"
    if vci < 0.35 or (not pd.isna(ndvi_z) and ndvi_z <= -1.0 and rain <= -20):
        return "stressed"
    if vci < 0.50 or (not pd.isna(ndvi_z) and ndvi_z <= -0.5):
        return "watch"
    return "healthy"


def add_anomalies(rows: pd.DataFrame, reference: pd.DataFrame) -> pd.DataFrame:
    """Return `rows` + ANOMALY_COLS, baselines from `reference` minus each row's own season_year."""
    nd = _yearly(reference, "NDVI_mean")
    rn = _yearly(reference, "rain_season_to_date_mm")
    sm = _yearly(reference, "soil_moisture_0_7cm")
    rec = {c: [] for c in ANOMALY_COLS}
    for r in rows[["region_id", "doy_bin", "season_year", "NDVI_mean", "rain_season_to_date_mm",
                   "soil_moisture_0_7cm"]].itertuples(index=False):
        key, year = (r.region_id, int(r.doy_bin)), int(r.season_year)
        # NDVI z + VCI (std ddof=1 needs >= 2 other years; VCI needs max > min)
        o, oy = _others(nd, key, year)
        z = v = np.nan
        if len(o) and not pd.isna(r.NDVI_mean):
            sd = float(np.std(o, ddof=1)) if len(o) > 1 else np.nan
            z = (r.NDVI_mean - float(np.mean(o))) / sd if sd and not np.isnan(sd) else np.nan
            rng = float(np.max(o) - np.min(o))
            v = float(np.clip((r.NDVI_mean - float(np.min(o))) / rng, 0, 1)) if rng > 0 else np.nan
        rec["ndvi_anomaly_z"].append(z)
        rec["vci"].append(v)
        rec["n_years_baseline"].append(int(len(o)))
        rec["baseline_year_min"].append(int(oy.min()) if len(oy) else None)
        rec["baseline_year_max"].append(int(oy.max()) if len(oy) else None)
        # rain normal + anomaly
        o, _ = _others(rn, key, year)
        normal = float(np.mean(o)) if len(o) and not pd.isna(r.rain_season_to_date_mm) else np.nan
        rec["rain_normal_mm"].append(normal)
        rec["rain_anomaly_pct"].append(100.0 * (r.rain_season_to_date_mm - normal) / normal
                                       if normal and not np.isnan(normal) else np.nan)
        # soil moisture z
        o, _ = _others(sm, key, year)
        smz = np.nan
        if len(o) > 1 and not pd.isna(r.soil_moisture_0_7cm):
            sd = float(np.std(o, ddof=1))
            smz = (r.soil_moisture_0_7cm - float(np.mean(o))) / sd if sd > 0 else np.nan
        rec["soil_moisture_anomaly_z"].append(smz)
        rec["stress_class_now"].append(stress_rule(v, rec["rain_anomaly_pct"][-1], z))
    out = rows.copy()
    for c in ANOMALY_COLS:
        out[c] = pd.Series(rec[c], index=rows.index, dtype=object if c in (
            "stress_class_now", "baseline_year_min", "baseline_year_max") else float)
    return out


def add_targets(df: pd.DataFrame) -> pd.DataFrame:
    """target_stress_class = next composite's stress_class_now in the same (region, season_year, crop_season),
    NaN across gaps > 24 days (as agri/03)."""
    df = df.sort_values(["region_id", "season_year", "crop_season", "period_end"]).reset_index(drop=True)
    grp = df.groupby(["region_id", "season_year", "crop_season"])
    df["target_stress_class"] = grp["stress_class_now"].shift(-1)
    gap = (grp["period_end"].shift(-1) - df["period_end"]).dt.days
    df.loc[gap > SEASON_GAP_DAYS, "target_stress_class"] = np.nan
    return df.sort_values(["region_id", "period_end"]).reset_index(drop=True)


def model_row(row: pd.Series | dict, class_to_id: dict[str, int]) -> dict[str, float]:
    """The raw feature dict the classifier consumes (keys = metadata feature names; order applied in predict.py)."""
    sc = row.get("stress_class_now")
    return {
        "stress_class_now_id": float(class_to_id[sc]) if isinstance(sc, str) and sc in class_to_id else np.nan,
        "NDVI_mean": row.get("NDVI_mean"), "ndvi_anomaly_z": row.get("ndvi_anomaly_z"), "vci": row.get("vci"),
        "ndvi_delta": row.get("ndvi_delta"), "rain_anomaly_pct": row.get("rain_anomaly_pct"),
        "rain_30d_mm": row.get("rain_30d_mm"), "soil_moisture_0_7cm": row.get("soil_moisture_0_7cm"),
        "soil_moisture_anomaly_z": row.get("soil_moisture_anomaly_z"), "doy_bin": row.get("doy_bin"),
    }


# ---------------------------------------------------------------- serving store
class FeatureStore:
    """History of composites for the service; builds one feature row for (region, cutoff, as_of)."""

    def __init__(self, modis_csv: Path, weather_csv: Path):
        self.modis_csv, self.weather_csv = Path(modis_csv), Path(weather_csv)
        self.history = build_history(load_modis(self.modis_csv), load_weather_daily(self.weather_csv))
        self._by_region = {rid: g.reset_index(drop=True) for rid, g in self.history.groupby("region_id")}
        self.latest_period_end = self.history["period_end"].max()
        self.first_period_end = self.history["period_end"].min()

    @property
    def regions(self) -> list[str]:
        return sorted(self._by_region)

    def latest(self, region_id: str) -> pd.Timestamp | None:
        g = self._by_region.get(region_id)
        return None if g is None or g.empty else g["period_end"].max()

    def row_at(self, region_id: str, cutoff: pd.Timestamp, as_of: pd.Timestamp | None = None,
               crop_season: str | None = None, offset: int = 0) -> dict | None:
        """Feature row of the latest composite with period_end <= cutoff (optionally of `crop_season`; `offset`
        steps back that many composites). Baselines: all other years, or with as_of only rows period_end <= as_of.
        Adds prev (trend) info. None if no composite qualifies."""
        g = self._by_region.get(region_id)
        if g is None:
            return None
        cand = g[g["period_end"] <= cutoff]
        if crop_season:
            cand = cand[cand["crop_season"] == crop_season]
        if len(cand) <= offset:
            return None
        pos = cand.index[-1 - offset]
        ref = self.history if as_of is None else self.history[self.history["period_end"] <= as_of]
        rows = g.loc[[pos - 1, pos]] if pos > 0 else g.loc[[pos]]
        feats = add_anomalies(rows, ref)
        cur = feats.loc[pos].to_dict()
        prev = feats.loc[pos - 1].to_dict() if pos > 0 else None
        same_season = (prev is not None and prev["crop_season"] == cur["crop_season"]
                       and prev["season_year"] == cur["season_year"]
                       and (cur["period_end"] - prev["period_end"]).days <= SEASON_GAP_DAYS)
        cur["prev"] = prev if same_season else None
        return cur
