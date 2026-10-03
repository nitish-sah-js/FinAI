from __future__ import annotations
import httpx, pandas as pd

URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
PARAMS = "PRECTOTCORR,T2M,T2M_MAX,RH2M,GWETROOT"


def fetch_sync(lat: float, lon: float, start: str, end: str, params: str = PARAMS) -> pd.DataFrame:
    """Importable by 09 Agri. start/end = YYYYMMDD. Returns daily DataFrame indexed by date (-999 -> NaN)."""
    r = httpx.get(URL, params={"parameters": params, "community": "AG", "latitude": lat, "longitude": lon,
                               "start": start, "end": end, "format": "JSON"}, timeout=60)
    r.raise_for_status()
    p = r.json()["properties"]["parameter"]
    df = pd.DataFrame(p)
    df.index = pd.to_datetime(df.index, format="%Y%m%d")
    return df.replace(-999, float("nan")).sort_index()


async def fetch(lat: float, lon: float, start: str, end: str, params: str = PARAMS) -> pd.DataFrame:
    import asyncio
    return await asyncio.to_thread(fetch_sync, lat, lon, start, end, params)
