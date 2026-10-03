"""Region metadata for the 8 districts the model was trained on (09 §3).

district / state come from the GEE (GAUL level-2) names in the MODIS export; lat/lon are the district centroids the
Open-Meteo weather was downloaded for (weather CSV columns latitude_requested / longitude_requested); crops come from
data/region_exposure.json (falls back to the 09 §3 table).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

# (GAUL state, GAUL district) -> region_id.  GAUL stores Kalaburagi under its old name Gulbarga.
GAUL_TO_REGION: dict[tuple[str, str], str] = {
    ("Maharashtra", "Yavatmal"): "MH-Yavatmal",
    ("Maharashtra", "Latur"): "MH-Latur",
    ("Madhya Pradesh", "Indore"): "MP-Indore",
    ("Madhya Pradesh", "Ujjain"): "MP-Ujjain",
    ("Gujarat", "Rajkot"): "GJ-Rajkot",
    ("Punjab", "Ludhiana"): "PB-Ludhiana",
    ("Karnataka", "Gulbarga"): "KA-Kalaburagi",
    ("Rajasthan", "Jodhpur"): "RJ-Jodhpur",
}

DISPLAY_DISTRICT = {"KA-Kalaburagi": "Kalaburagi"}          # modern name for display

SPEC_CROPS = {                                                # 09 §3 table (fallback only)
    "MH-Yavatmal": ["cotton", "soybean"], "MH-Latur": ["soybean", "tur"], "MP-Indore": ["soybean", "wheat"],
    "MP-Ujjain": ["soybean"], "GJ-Rajkot": ["groundnut", "cotton"], "PB-Ludhiana": ["rice", "wheat"],
    "KA-Kalaburagi": ["tur"], "RJ-Jodhpur": ["bajra"],
}


def region_meta_from_weather(weather_csv: Path, exposure: dict | None = None) -> dict[str, dict]:
    """region_id -> {region_id, district, state, lat, lon, main_crops}."""
    cols = ["region", "latitude_requested", "longitude_requested"]
    w = pd.read_csv(weather_csv, usecols=cols).drop_duplicates("region")
    latlon = {r.region: (float(r.latitude_requested), float(r.longitude_requested)) for r in w.itertuples()}
    exp_regions = (exposure or {}).get("regions", {})
    out: dict[str, dict] = {}
    for (state, gaul_district), rid in GAUL_TO_REGION.items():
        lat, lon = latlon.get(rid, (None, None))
        crops = (exp_regions.get(rid) or {}).get("crops") or SPEC_CROPS.get(rid, [])
        out[rid] = {"region_id": rid, "district": DISPLAY_DISTRICT.get(rid, gaul_district), "gaul_district": gaul_district,
                    "state": state, "lat": lat, "lon": lon, "main_crops": list(crops)}
    return out


@lru_cache
def _cached(weather_csv: str) -> dict[str, dict]:
    from .exposure import load_exposure
    return region_meta_from_weather(Path(weather_csv), load_exposure())


def get_regions(weather_csv: Path) -> dict[str, dict]:
    return _cached(str(weather_csv))
