"""Open-Meteo wrappers. ALL variable-name differences between endpoints live in SOIL_VARS / DAILY_*."""
from __future__ import annotations
import asyncio
import httpx, pandas as pd

FORECAST = "https://api.open-meteo.com/v1/forecast"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
HISTFC = "https://historical-forecast-api.open-meteo.com/v1/forecast"

SOIL_VARS = {  # VERIFY on day 0 against open-meteo.com/en/docs
    "forecast": ["soil_moisture_0_to_1cm", "soil_moisture_1_to_3cm", "soil_moisture_3_to_9cm"],
    "historical_forecast": ["soil_moisture_0_to_1cm", "soil_moisture_1_to_3cm", "soil_moisture_3_to_9cm"],
    "archive": ["soil_moisture_0_to_7cm"],
}
DAILY_FC = "precipitation_sum,temperature_2m_max,relative_humidity_2m_mean,wind_speed_10m_max"
DAILY_ARCHIVE = "precipitation_sum,temperature_2m_max,wind_speed_10m_max"  # no RH -> handler assumes 60%


async def _get(url: str, params: dict, retries: int = 2) -> dict:
    last = None
    for i in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(url, params=params)
                r.raise_for_status()
                return r.json()
        except Exception as e:
            last = e
            await asyncio.sleep(1 + i)
    raise last


async def forecast(lat: float, lon: float, days: int = 7) -> dict:
    return await _get(FORECAST, {"latitude": lat, "longitude": lon, "daily": DAILY_FC,
                                 "hourly": ",".join(SOIL_VARS["forecast"]),
                                 "forecast_days": days, "timezone": "Asia/Kolkata"})


async def historical_forecast(lat: float, lon: float, start: str, end: str) -> dict:
    return await _get(HISTFC, {"latitude": lat, "longitude": lon, "daily": DAILY_FC,
                               "hourly": ",".join(SOIL_VARS["historical_forecast"]),
                               "start_date": start, "end_date": end, "timezone": "Asia/Kolkata"})


async def archive(lat: float, lon: float, start: str, end: str, soil: bool = True) -> dict:
    p = {"latitude": lat, "longitude": lon, "daily": DAILY_ARCHIVE,
         "start_date": start, "end_date": end, "timezone": "Asia/Kolkata"}
    if soil:
        p["hourly"] = ",".join(SOIL_VARS["archive"])
    return await _get(ARCHIVE, p)


def parse_daily(js: dict) -> list[dict]:
    d = js["daily"]
    n = len(d["time"])
    g = lambda k: d.get(k) or [None] * n
    return [{"date": d["time"][i], "precip_mm": g("precipitation_sum")[i], "tmax_c": g("temperature_2m_max")[i],
             "rh_pct": g("relative_humidity_2m_mean")[i], "wind_kmh": g("wind_speed_10m_max")[i]}
            for i in range(n)]


def soil_day0(js: dict, kind: str) -> float | None:
    """Mean 0-7 cm soil moisture over the first day. Forecast layers (0-1, 1-3, 3-9 cm) are
    thickness-weighted into 0-7 cm (1,2,4 cm); ERA5 already provides 0-7 cm.
    NOTE: forecast-model and ERA5 soil moisture are different products, so the z-score is approximate."""
    h = js.get("hourly") or {}
    names = SOIL_VARS[kind]
    cols = [h.get(n) for n in names]
    if any(c is None for c in cols):
        return None
    vals = []
    for i in range(min(24, len(cols[0]))):
        row = [c[i] for c in cols]
        if any(v is None for v in row):
            continue
        vals.append(row[0] if len(row) == 1 else (row[0] * 1 + row[1] * 2 + row[2] * 4) / 7)
    return round(sum(vals) / len(vals), 4) if vals else None


async def climatology(lat: float, lon: float, y0: int = 2001, y1: int = 2025, soil_y0: int = 2011) -> dict:
    """Cached forever per region. JSON-serialisable (daily precip 2001-2025 + daily-mean soil moisture)."""
    daily = await archive(lat, lon, f"{y0}-01-01", f"{y1}-12-31", soil=False)
    out = {"dates": daily["daily"]["time"], "precip": daily["daily"]["precipitation_sum"],
           "soil_dates": [], "soil": []}
    try:
        h = await _get(ARCHIVE, {"latitude": lat, "longitude": lon, "hourly": SOIL_VARS["archive"][0],
                                 "start_date": f"{soil_y0}-01-01", "end_date": f"{y1}-12-31"})
        s = pd.Series(h["hourly"][SOIL_VARS["archive"][0]], index=pd.to_datetime(h["hourly"]["time"]), dtype=float)
        dm = s.resample("D").mean().dropna()
        out["soil_dates"] = [d.strftime("%Y-%m-%d") for d in dm.index]
        out["soil"] = [round(float(v), 4) for v in dm.values]
    except Exception:
        pass
    return out
