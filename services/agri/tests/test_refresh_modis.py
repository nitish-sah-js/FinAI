"""refresh_modis.py: offline parts (boundary selection, AppEEARS statistics -> table columns, bias check)."""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "refresh_modis.py"
spec = importlib.util.spec_from_file_location("refresh_modis", SCRIPT)
rm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rm)

SQUARE = {"type": "Polygon", "coordinates": [[[70, 20], [71, 20], [71, 21], [70, 21], [70, 20]]]}
GEO = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": SQUARE, "properties": {"shapeName": n}} for n in rm.DISTRICTS.values()]
    + [{"type": "Feature", "geometry": SQUARE, "properties": {"shapeName": "Elsewhere"}}]}


def test_district_features_carry_table_names_in_order():
    fc = rm.district_features(GEO)
    assert len(fc["features"]) == 8
    assert fc["features"][1]["properties"] == {"ADM1_NAME": "Karnataka", "ADM2_NAME": "Gulbarga"}


def test_missing_district_is_an_error_not_a_gap():
    with pytest.raises(ValueError):
        rm.district_features({"type": "FeatureCollection", "features": GEO["features"][:3]})


def test_stats_to_table_matches_existing_columns_and_scaling():
    fc = rm.district_features(GEO)
    rows = []
    for aid in (1, 2):
        for layer, mean in (("_250m_16_days_NDVI", 0.45), ("_250m_16_days_EVI", 3100)):   # EVI given as raw ints
            rows.append({"File Name": "x", "Dataset": layer, "aid": f"aid000{aid}", "Date": "2026-01-17",
                         "Count": 1000 + aid, "Mean": mean, "Maximum": mean * 1.5, "Standard Deviation": mean / 5})
    out = rm.stats_to_table(pd.DataFrame(rows), fc)
    existing = pd.read_csv(rm.MODIS_CSV, nrows=1).columns.tolist()
    assert out.columns.tolist() == existing
    r = out[out["ADM2_NAME"] == "Rajkot"].iloc[0]
    assert r["NDVI_mean"] == 0.45 and abs(r["EVI_mean"] - 0.31) < 1e-9 and r["NDVI_count"] == 1001
    assert str(r["period_end"].date()) == "2026-02-01"          # image_date + 15 days


def test_bias_is_measured_on_overlapping_composites():
    old = pd.DataFrame({"ADM1_NAME": ["Gujarat"], "ADM2_NAME": ["Rajkot"], "image_date": ["2025-12-19"], "NDVI_mean": [0.40]})
    new = pd.DataFrame({"ADM1_NAME": ["Gujarat"], "ADM2_NAME": ["Rajkot"], "image_date": pd.to_datetime(["2025-12-19"]),
                        "NDVI_mean": [0.42]})
    bias, n = rm.ndvi_bias(new, old)
    assert n == 1 and abs(bias - 0.02) < 1e-9


def test_without_credentials_nothing_is_downloaded(monkeypatch, capsys):
    monkeypatch.setattr(rm, "load_env", lambda: ("", ""))
    monkeypatch.setattr("sys.argv", ["refresh_modis.py"])
    assert rm.main() == 2
    assert "NEEDS HUMAN INPUT" in capsys.readouterr().out
