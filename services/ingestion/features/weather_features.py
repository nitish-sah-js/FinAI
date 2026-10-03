"""Pure functions (spec section 6). No I/O, no copilot_common."""
from __future__ import annotations
import math
from datetime import date
import numpy as np
import pandas as pd


def rain_anomaly_pct(forecast_mm: float, clim_mm: float) -> float:
    return round(100 * (forecast_mm - clim_mm) / max(clim_mm, 1.0), 1)


def heat_index_c(t_c: float, rh: float) -> float:
    t = t_c * 9 / 5 + 32
    hi = (-42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh
          - 0.00683783 * t * t - 0.05481717 * rh * rh + 0.00122874 * t * t * rh
          + 0.00085282 * t * rh * rh - 0.00000199 * t * t * rh * rh)
    if rh < 13 and 80 <= t <= 112:
        hi -= ((13 - rh) / 4) * math.sqrt((17 - abs(t - 95)) / 17)
    elif rh > 85 and 80 <= t <= 87:
        hi += ((rh - 85) / 10) * ((87 - t) / 5)
    if hi < 80:  # Steadman simple formula
        hi = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + rh * 0.094)
    return round((hi - 32) * 5 / 9, 1)


def soil_moisture_z(current: float, clim_mean: float, clim_std: float) -> float:
    return (current - clim_mean) / max(clim_std, 1e-6)


def saffir_simpson(max_wind_kt: float) -> int:
    for lo, cat in ((137, 5), (113, 4), (96, 3), (83, 2), (64, 1)):
        if max_wind_kt >= lo:
            return cat
    return 0


def weather_confidence(source_ok: bool, horizon_days: int, lookahead_risk: bool) -> float:
    c = 0.85 if source_ok else 0.4
    c -= 0.05 * max(0, horizon_days - 3)
    if lookahead_risk:
        c *= 0.8
    return round(max(0.1, min(c, 0.95)), 2)


def weather_alerts(rain_anomaly: float | None, max_daily_mm: float, max_temp_c: float,
                   heat_index: float, storm: dict | None, month: int, country: str = "IN") -> list[str]:
    a = []
    if max_daily_mm > 115.6 or (rain_anomaly is not None and rain_anomaly > 100):
        a.append("heavy_rain")
    if rain_anomaly is not None and rain_anomaly < -40 and 6 <= month <= 9:
        a.append("rain_deficit")
    if max_temp_c >= 45 or heat_index >= 41:
        a.append("heatwave")
    if storm:
        a.insert(0, "hurricane" if storm.get("basin") in ("ATL", "EPAC") or country == "US" else "cyclone")
    return a


def window_clim_mm(daily: pd.Series, start: date, days: int, years=range(2001, 2026)) -> float | None:
    """Mean over `years` of the precipitation SUM in the same calendar window [start, start+days)."""
    sums = []
    for y in years:
        try:
            s = pd.Timestamp(date(y, start.month, start.day))
        except ValueError:  # Feb 29
            continue
        w = daily[(daily.index >= s) & (daily.index < s + pd.Timedelta(days=days))].dropna()
        if len(w) >= days * 0.9:
            sums.append(float(w.sum()))
    return float(np.mean(sums)) if sums else None


def soil_clim_stats(series: pd.Series, doy: int, window: int = 7) -> tuple[float | None, float | None]:
    s = series.dropna()
    sel = s[(s.index.dayofyear >= doy - window) & (s.index.dayofyear <= doy + window)]
    if len(sel) < 10:
        return None, None
    return float(sel.mean()), float(sel.std())
