"""Fetch India's policy repo rate and CPI inflation from official sources into services/ingestion/config.

    .venv/Scripts/python -m services.ingestion.scripts.refresh_india_macro          (from the repo root)

Sources (no API key needed):
  repo rate  BIS central bank policy rates, series WS_CBPOL D.IN (daily): RBI's official repo rate
  CPI YoY    OECD Main Economic Indicators via FRED, series CPALTT01INM659N (CPI all items, % change on a year
             earlier, monthly). It currently ends in March 2025; MoSPI's own API could not be used (undocumented).

Every row stores source, source_url, as_of (the period the value describes) and retrieved_at. CPI rows also carry
`date` = the day the figure became public (period end + 12 days, MoSPI's usual release day), so a backtest never
sees a CPI value before it was released. If a fetch fails, that file is left with no data rows and a MISSING note,
and the series is reported as missing; nothing is filled in.
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd

CFG = Path(__file__).resolve().parents[1] / "config"
BIS_URL = "https://stats.bis.org/api/v1/data/WS_CBPOL/D.IN?format=csv&startPeriod=2000-01-01"
FRED_CPI = "CPALTT01INM659N"
FRED_URL = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={FRED_CPI}"
CPI_RELEASE_LAG_DAYS = 12
REPO_SINCE = "2001-04-03"     # BIS: "from 3 Apr 2001 onwards: official repo overnight rate"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_repo(client: httpx.Client) -> list[dict]:
    r = client.get(BIS_URL)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))[["TIME_PERIOD", "OBS_VALUE"]].dropna()
    df["TIME_PERIOD"] = pd.to_datetime(df["TIME_PERIOD"])
    df = df.sort_values("TIME_PERIOD")
    df = df[df["TIME_PERIOD"] >= REPO_SINCE]                          # before this the BIS series is the bank rate
    changes = df[df["OBS_VALUE"].diff().fillna(1) != 0]            # keep only the days the rate changed
    if changes.empty:
        raise ValueError("BIS returned no observations")
    got, last_obs = _now(), df["TIME_PERIOD"].iloc[-1].date().isoformat()
    rows = [{"date": d.date().isoformat(), "repo_rate": round(float(v), 4), "as_of": d.date().isoformat(),
             "source": "BIS central bank policy rates (RBI repo rate)", "source_url": BIS_URL, "retrieved_at": got}
            for d, v in zip(changes["TIME_PERIOD"], changes["OBS_VALUE"])]
    rows[-1]["last_observed"] = last_obs                              # BIS confirms the rate still held on this day
    return rows


def fetch_cpi(client: httpx.Client) -> pd.DataFrame:
    r = client.get(FRED_URL)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["period", "cpi_yoy"]
    df = df.dropna()
    df["cpi_yoy"] = pd.to_numeric(df["cpi_yoy"], errors="coerce")
    df = df.dropna()
    if df.empty:
        raise ValueError("FRED returned no observations")
    period = pd.to_datetime(df["period"])
    month_end = period + pd.offsets.MonthEnd(0)
    out = pd.DataFrame({
        "date": (month_end + pd.Timedelta(days=CPI_RELEASE_LAG_DAYS)).dt.date.astype(str),   # when it became public
        "cpi_yoy": df["cpi_yoy"].round(2),
        "as_of": month_end.dt.date.astype(str),                                               # the month it describes
        "source": f"OECD MEI India CPI all items via FRED ({FRED_CPI}); not MoSPI headline CPI-combined",
        "source_url": FRED_URL,
        "retrieved_at": _now(),
    })
    return out[out["as_of"] >= "2000-01-01"]


def main() -> int:
    missing: list[str] = []
    client = httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "nit-raipur-copilot/1.0"})
    try:
        repo = fetch_repo(client)
        (CFG / "policy_rates.json").write_text(json.dumps(repo, indent=1) + "\n", encoding="utf-8")
        print(f"repo rate: {len(repo)} changes, latest {repo[-1]['repo_rate']}% from {repo[-1]['date']} "
              f"(still in force on {repo[-1]['last_observed']})")
    except Exception as e:  # noqa: BLE001
        (CFG / "policy_rates.json").write_text("[]\n", encoding="utf-8")
        missing.append(f"repo rate: BIS fetch failed ({type(e).__name__}: {e})")
    try:
        cpi = fetch_cpi(client)
        with open(CFG / "cpi_india.csv", "w", encoding="utf-8", newline="") as f:
            f.write(f"# India CPI inflation, % change on a year earlier. Source: OECD MEI via FRED {FRED_CPI}.\n"
                    f"# date = when the figure became public (month end + {CPI_RELEASE_LAG_DAYS} days); as_of = month described.\n")
            cpi.to_csv(f, index=False)
        print(f"CPI: {len(cpi)} months, latest {cpi['cpi_yoy'].iloc[-1]}% for {cpi['as_of'].iloc[-1]}")
        if cpi["as_of"].iloc[-1] < (datetime.now(timezone.utc) - timedelta(days=120)).date().isoformat():
            missing.append(f"CPI after {cpi['as_of'].iloc[-1]}: the OECD series on FRED has no newer months, and "
                           "MoSPI's API could not be used; add newer MoSPI CPI rows by hand or supply an API route")
    except Exception as e:  # noqa: BLE001
        with open(CFG / "cpi_india.csv", "w", encoding="utf-8", newline="") as f:
            f.write(f"# MISSING: fetch failed on {_now()} ({type(e).__name__}). No values are filled in.\n"
                    "date,cpi_yoy,as_of,source,source_url,retrieved_at\n")
        missing.append(f"CPI: FRED fetch failed ({type(e).__name__}: {e})")
    for m in missing:
        print("NEEDS HUMAN INPUT:", m)
    return 1 if any("failed" in m for m in missing) else 0


if __name__ == "__main__":
    sys.exit(main())
