from __future__ import annotations
import json
from datetime import datetime, timezone
import httpx, pandas as pd
from ..features.macro_features import pct_change_n, bps_change
from ..store import data_dir

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"


async def fred_series(series_id: str = "DGS10") -> list[list]:
    """No key needed. JSON-friendly [[date, value], ...] so it can go through the cache."""
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(FRED_CSV, params={"id": series_id})
        r.raise_for_status()
    df = pd.read_csv(pd.io.common.StringIO(r.text))
    df.columns = ["date", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna()
    df = df[df["date"] >= "2000-01-01"]  # full history so as_of (backtest) runs get a value too
    return [[d, float(v)] for d, v in zip(df["date"], df["value"])]


def to_series(pairs: list[list]) -> pd.Series:
    return pd.Series([p[1] for p in pairs], index=pd.to_datetime([p[0] for p in pairs]), dtype=float)


def load_policy(cfg_dir) -> list[dict]:
    return sorted(json.loads((cfg_dir / "policy_rates.json").read_text()), key=lambda r: r["date"])


def load_cpi(cfg_dir) -> pd.DataFrame:
    df = pd.read_csv(cfg_dir / "cpi_india.csv", comment="#", parse_dates=["date"])
    return df.sort_values("date")


def build_macro(inr: pd.Series | None, brent: pd.Series | None, us10y: pd.Series | None,
                policy: list[dict], cpi: pd.DataFrame, as_of: datetime | None = None):
    """Pure function. Returns (value, series_dates, warnings, n_missing). Uses last value with date <= as_of."""
    cutoff = pd.Timestamp(as_of.astimezone(timezone.utc).replace(tzinfo=None)) if as_of else None

    def cut(s):
        if s is None:
            return None
        s = s.dropna()
        if cutoff is not None:
            s = s[s.index <= cutoff]
        return s if len(s) else None

    v, dates, warns = {}, {}, []
    for name, s in (("usd_inr", cut(inr)), ("brent", cut(brent)), ("us10y", cut(us10y))):
        if s is None:
            v[name] = v[f"{name}_chg_5d"] = None
            warns.append(f"{name} unavailable")
            continue
        v[name] = round(float(s.iloc[-1]), 4)
        chg = pct_change_n(s, 5)
        v[f"{name}_chg_5d"] = round(chg, 4) if chg is not None else None
        dates[name] = s.index[-1].strftime("%Y-%m-%d")

    pol = [p for p in policy if cutoff is None or pd.Timestamp(p["date"]) <= cutoff]
    if pol:
        v["repo_rate"] = pol[-1]["repo_rate"]
        v["repo_change_bps"] = bps_change(pol[-1]["repo_rate"], pol[-2]["repo_rate"]) if len(pol) > 1 else 0
        dates["repo_rate"] = pol[-1]["date"]
    else:
        v["repo_rate"] = v["repo_change_bps"] = None
        warns.append("repo_rate unavailable")
    c = cpi[cpi["date"] <= cutoff] if cutoff is not None else cpi
    if len(c):
        v["cpi_yoy"] = float(c.iloc[-1]["cpi_yoy"])
        dates["cpi_yoy"] = c.iloc[-1]["date"].strftime("%Y-%m-%d")
        month = str(c.iloc[-1]["as_of"]) if "as_of" in c.columns and pd.notna(c.iloc[-1]["as_of"]) else dates["cpi_yoy"]
        v["cpi_as_of"] = month                                   # the month the figure describes
        ref = cutoff if cutoff is not None else pd.Timestamp.now(tz="UTC").tz_localize(None)
        age_days = (ref - pd.Timestamp(month)).days
        if age_days > 75:                                        # newer than ~2 releases is expected
            warns.append(f"cpi_yoy is old: latest available figure is for {month[:7]} ({age_days // 30} months ago)")
    else:
        v["cpi_yoy"] = None
        warns.append("cpi_yoy unavailable")
    v["series_dates"] = dates
    n_missing = sum(1 for k in ("usd_inr", "brent", "us10y", "repo_rate", "cpi_yoy") if v[k] is None)
    return v, dates, warns, n_missing
