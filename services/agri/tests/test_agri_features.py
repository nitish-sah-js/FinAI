"""Feature recipe: serve-time rows == training table, season-safe delta, as_of / no-lookahead."""
import numpy as np
import pandas as pd
import pytest

from agri.features import FeatureStore, add_anomalies, model_row

FEATS = ["NDVI_mean", "ndvi_anomaly_z", "vci", "ndvi_delta", "rain_anomaly_pct", "rain_30d_mm",
         "soil_moisture_0_7cm", "soil_moisture_anomaly_z", "doy_bin"]
KNOWN = [("MH-Yavatmal", "2015-09-13"), ("MH-Latur", "2015-08-28"), ("GJ-Rajkot", "2011-02-01"),
         ("PB-Ludhiana", "2020-01-16"), ("RJ-Jodhpur", "2026-01-03"), ("KA-Kalaburagi", "2018-10-15"), ("MP-Ujjain", "2016-06-08"), ("MP-Ujjain", "2016-06-24")]


def _close(a, b):
    if pd.isna(a) and pd.isna(b):
        return True
    return np.isclose(float(a), float(b), rtol=1e-9, atol=1e-9)


@pytest.mark.parametrize("region,period_end", KNOWN)
def test_serving_row_matches_training_csv(store, training_csv, region, period_end):
    pe = pd.Timestamp(period_end)
    exp = training_csv[(training_csv.region_id == region) & (training_csv.period_end == pe)]
    assert len(exp) == 1, f"{region} {period_end} not in training table"
    exp = exp.iloc[0]
    row = store.row_at(region, pe)                       # latest composite <= its own period_end = that composite
    assert row["period_end"] == pe
    for f in FEATS:
        assert _close(row[f], exp[f]), f"{f}: serve {row[f]} != train {exp[f]}"
    assert (row["stress_class_now"] if isinstance(row["stress_class_now"], str) else None) == \
        (exp["stress_class_now"] if isinstance(exp["stress_class_now"], str) else None)


def test_whole_table_matches(store, training_csv):
    """Every training row, recomputed with the serving code, equals agri_training.csv."""
    feats = add_anomalies(store.history, store.history)
    m = feats.merge(training_csv, on=["region_id", "period_end"], suffixes=("", "_csv"), validate="one_to_one")
    assert len(m) == len(training_csv)
    for f in FEATS:
        a, b = m[f].astype(float), m[f"{f}_csv"].astype(float)
        assert np.allclose(a, b, rtol=1e-9, atol=1e-9, equal_nan=True), f


def test_ndvi_delta_is_season_safe(store):
    h = store.history
    first = h.groupby(["region_id", "season_year", "crop_season"]).head(1)
    assert first["ndvi_delta"].isna().all(), "first composite of a season must have NaN delta"
    # never across the Apr-May gap: every non-NaN delta has a previous composite <= 24 days before
    g = h.groupby("region_id")
    gap = (h["period_end"] - g["period_end"].shift(1)).dt.days
    assert (gap[h["ndvi_delta"].notna()] <= 24).all()
    same = (g["crop_season"].shift(1) == h["crop_season"]) & (g["season_year"].shift(1) == h["season_year"])
    assert same[h["ndvi_delta"].notna()].all()


def test_as_of_excludes_later_composites(store):
    as_of = pd.Timestamp("2015-09-20")
    # main.py passes cutoff = min(date, as_of); as_of additionally restricts the baselines to period_end <= as_of
    row = store.row_at("MH-Latur", min(pd.Timestamp("2026-06-01"), as_of), as_of=as_of)
    assert row["period_end"] <= as_of
    assert row["period_end"] == pd.Timestamp("2015-09-13")
    assert row["baseline_year_max"] <= 2014                 # only years before as_of's season year
    assert row["n_years_baseline"] == 4                     # 2011-2014
    assert row["prev"] is None or row["prev"]["period_end"] < row["period_end"]


def test_no_lookahead_future_data_cannot_change_features(store):
    """Scramble every composite and weather value after as_of; the as_of feature row must not change."""
    as_of = pd.Timestamp("2016-08-15")
    before = store.row_at("MH-Yavatmal", as_of, as_of=as_of)
    rng = np.random.default_rng(0)
    scrambled = FeatureStore.__new__(FeatureStore)
    h = store.history.copy()
    fut = h["period_end"] > as_of
    for c in ["NDVI_mean", "rain_season_to_date_mm", "soil_moisture_0_7cm", "rain_30d_mm", "ndvi_delta"]:
        h.loc[fut, c] = rng.normal(size=int(fut.sum()))
    scrambled.history = h
    scrambled._by_region = {r: g.reset_index(drop=True) for r, g in h.groupby("region_id")}
    after = scrambled.row_at("MH-Yavatmal", as_of, as_of=as_of)
    assert before["period_end"] == after["period_end"] <= as_of
    for f in FEATS + ["stress_class_now"]:
        assert _close(before[f], after[f]) if not isinstance(before[f], str) else before[f] == after[f], f


def test_live_mode_uses_latest_composite(store):
    row = store.row_at("PB-Ludhiana", pd.Timestamp("2026-10-03"))
    assert row["period_end"] == store.latest_period_end == pd.Timestamp("2026-01-03")
    assert row["crop_season"] == "rabi" and row["season_year"] == 2026


def test_model_row_has_metadata_features(store, model):
    row = store.row_at("MP-Indore", pd.Timestamp("2019-09-30"))
    feats = model_row(row, {c: i for i, c in enumerate(model.classes)})
    assert set(feats) == set(model.features)


def test_teammate_table_parity_except_delta(store):
    """Trust-but-verify: our recipe reproduces the teammate's agri_training.csv except the fixed ndvi_delta."""
    from pathlib import Path
    p = Path(__file__).resolve().parents[3] / "_archive" / "agri" / "data" / "agri" / "agri_training.csv"   # moved 2026-10-04
    if not p.is_file():
        pytest.skip("teammate table not present")
    old = pd.read_csv(p, parse_dates=["period_end"])
    feats = add_anomalies(store.history, store.history)
    m = feats.merge(old, on=["region_id", "period_end"], suffixes=("", "_old"))
    assert len(m) == len(old)
    for f in ["ndvi_anomaly_z", "vci", "rain_anomaly_pct", "rain_30d_mm", "soil_moisture_anomaly_z"]:
        assert np.allclose(m[f].astype(float), m[f"{f}_old"].astype(float), equal_nan=True), f
    differ = ~np.isclose(m["ndvi_delta"], m["ndvi_delta_old"], equal_nan=True)
    assert 0 < differ.sum() <= 300                         # only season-boundary rows changed (240 expected)
    assert m.loc[differ, "ndvi_delta"].isna().all()
