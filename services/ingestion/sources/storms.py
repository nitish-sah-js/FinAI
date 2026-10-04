from __future__ import annotations
import json, math, os
from datetime import datetime
from pathlib import Path
import httpx
from copilot_common.settings import get_settings
from ..features.weather_features import saffir_simpson
from ..timeutil import parse_dt
from .. import store

NHC_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


async def nhc_current() -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(NHC_URL)
        r.raise_for_status()
    out = []
    for s in r.json().get("activeStorms", []):
        kt = float(s.get("intensity") or 0)
        cat = saffir_simpson(kt)
        out.append({"name": s.get("name"), "basin": "ATL" if s.get("binNumber", "AT").startswith("AT") else "EPAC",
                    "category": f"H{cat}" if cat else s.get("classification", "TS"),
                    "lat": float(s.get("latitudeNumeric") or 0), "lon": float(s.get("longitudeNumeric") or 0),
                    "max_wind_kt": kt, "track_toward": [], "source": "NOAA NHC CurrentStorms.json"})
    return out


def manual_storms(as_of: datetime | None = None) -> list[dict]:
    """data/storms.json is hand-entered from IMD RSMC bulletins. Optional 'valid_from' is honoured for as_of."""
    p = store.data_dir() / "storms.json"
    if not p.exists():
        return []
    storms = json.loads(p.read_text())
    if not get_settings().DEMO_MODE:                 # synthetic demo storms exist only with DEMO_MODE=1
        storms = [s for s in storms if not s.get("synthetic")]
    if as_of is None:
        return storms
    return [s for s in storms if s.get("valid_from") and parse_dt(s["valid_from"]) <= as_of]


def attach_storm(region: dict, storms: list[dict]) -> dict | None:
    best = None
    for s in storms:
        d = haversine_km(region["lat"], region["lon"], s["lat"], s["lon"])
        if d < 500 or region.get("region_id") in s.get("track_toward", []):
            if best is None or d < best["distance_km"]:
                best = {"name": s["name"], "category": s["category"], "basin": s["basin"],
                        "track_toward": s.get("track_toward", []), "distance_km": round(d)}
                if s.get("synthetic"):
                    best["synthetic"] = True
    return best
