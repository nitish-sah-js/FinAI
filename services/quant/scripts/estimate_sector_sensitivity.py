"""Estimate sector sensitivities to crude and USD/INR from market data, replacing judgment values where stable.

    .venv/Scripts/python services/quant/scripts/estimate_sector_sensitivity.py [--years 5] [--dry-run]

Model (per sector, weekly log returns, last N years, yfinance):
    r_sector = a + b_nifty * r_nifty + b_crude * r_brent + b_fx * r_usdinr + e
b_crude and b_fx are partial effects beyond the market move, which is how the scenario engine applies them (a Nifty
shock is a separate input). Each estimate is shrunk toward the judgment prior with w = t^2 / (t^2 + 4) (the rule
quant/scenario.py already uses). It replaces the judgment value only when it is STABLE: |t| >= 2 and the same sign in
both halves of the sample. Otherwise the judgment value stays and is labelled as such.

The heatmap value is |beta| / scale[factor] (data/sector_sensitivity.json "_scale"), clipped to 0..1, and the sign
goes into "_signs". Weather, agri and rates columns are not estimable from these regressions and stay judgment.
Writes data/sector_sensitivity.json (with "_provenance" and "_measured") and data/sector_sensitivity_report.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"
# Yahoo has full history for Bank, Pharma and IT indices only (^CNXFMCG, ^CNXENERGY, ^CNXAUTO and ^CNXMETAL return
# about one week), so FMCG and Auto use ETFs that track their Nifty index and Energy / Metals use an equal-weight basket
# of large constituents. The report names the proxy used for each sector.
SECTOR_SOURCE: dict[str, tuple[str, list[str]]] = {
    "Banks": ("index ^NSEBANK", ["^NSEBANK"]),
    "Pharma": ("index ^CNXPHARMA", ["^CNXPHARMA"]),
    "IT": ("index ^CNXIT", ["^CNXIT"]),
    "FMCG": ("ETF FMCGIETF.NS (tracks Nifty FMCG)", ["FMCGIETF.NS"]),
    "Auto": ("ETF AUTOBEES.NS (tracks Nifty Auto)", ["AUTOBEES.NS"]),
    "Energy": ("equal-weight basket", ["RELIANCE.NS", "ONGC.NS", "NTPC.NS", "POWERGRID.NS", "COALINDIA.NS"]),
    "Metals": ("equal-weight basket", ["TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "VEDL.NS", "NMDC.NS"]),
}
FACTORS = {"nifty": "^NSEI", "crude": "BZ=F", "usd_inr": "INR=X"}
MEASURED = ("crude", "usd_inr")                    # heatmap columns this regression can measure
SHRINK_K = 4.0


def weekly_returns(tickers: list[str], years: int) -> pd.DataFrame:
    raw = yf.download(tickers, period=f"{years}y", interval="1d", auto_adjust=True, progress=False)["Close"]
    wk = raw.resample("W-FRI").last()
    return np.log(wk).diff().dropna(how="all")


def ols(y: pd.Series, X: pd.DataFrame) -> dict:
    d = pd.concat([y, X], axis=1).dropna()
    yv, Xv = d.iloc[:, 0].values, np.column_stack([np.ones(len(d)), d.iloc[:, 1:].values])
    beta, *_ = np.linalg.lstsq(Xv, yv, rcond=None)
    resid = yv - Xv @ beta
    n, k = Xv.shape
    s2 = resid @ resid / max(n - k, 1)
    cov = s2 * np.linalg.inv(Xv.T @ Xv)
    se = np.sqrt(np.diag(cov))
    r2 = 1 - (resid @ resid) / (((yv - yv.mean()) ** 2).sum() or 1)
    names = ["const"] + list(X.columns)
    return {"n": int(n), "r2": float(r2), "beta": dict(zip(names, beta.tolist())), "se": dict(zip(names, se.tolist()))}


def judgment_beta(sens: dict, sector: str, factor: str) -> float:
    s = sens[sector][factor]
    sign = sens["_signs"][factor].get(sector, sens["_signs"][factor]["default"])
    return s * sign * sens["_scale"][factor]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    path = DATA / "sector_sensitivity.json"
    sens = json.loads(path.read_text(encoding="utf-8"))

    syms = sorted({t for _, ts in SECTOR_SOURCE.values() for t in ts} | set(FACTORS.values()))
    rets = weekly_returns(syms, args.years)
    X = rets[[FACTORS["nifty"], FACTORS["crude"], FACTORS["usd_inr"]]].copy()
    X.columns = ["nifty", "crude", "usd_inr"]
    report, provenance, measured = {}, {}, {}
    half = len(rets) // 2
    for sector, (label, members) in SECTOR_SOURCE.items():
        cols = [m for m in members if m in rets and rets[m].dropna().size >= 60]
        if not cols:
            report[sector] = {"index": label, "status": "no data"}
            continue
        y = rets[cols].mean(axis=1, skipna=False) if len(cols) > 1 else rets[cols[0]]      # equal-weight basket
        y = y.rename(sector)
        full = ols(y, X)
        a, b = ols(y.iloc[:half], X.iloc[:half]), ols(y.iloc[half:], X.iloc[half:])
        rows = {}
        for f in MEASURED:
            beta, se = full["beta"][f], full["se"][f]
            t = beta / se if se else 0.0
            same_sign = np.sign(a["beta"][f]) == np.sign(b["beta"][f]) == np.sign(beta)
            stable = abs(t) >= 2 and bool(same_sign)
            prior = judgment_beta(sens, sector, f)
            w = t * t / (t * t + SHRINK_K)
            shrunk = w * beta + (1 - w) * prior
            rows[f] = {"beta_ols": round(beta, 4), "se": round(se, 4), "t": round(t, 2), "beta_shrunk": round(shrunk, 4),
                       "judgment_beta": round(prior, 4), "half1": round(a["beta"][f], 4), "half2": round(b["beta"][f], 4),
                       "stable": stable}
            if stable:
                sens[sector][f] = round(min(1.0, abs(shrunk) / sens["_scale"][f]), 2)
                sign = 1 if shrunk > 0 else -1
                if sign != sens["_signs"][f]["default"]:
                    sens["_signs"][f][sector] = sign
                else:
                    sens["_signs"][f].pop(sector, None)
                provenance.setdefault(sector, {})[f] = "measured"
            else:
                provenance.setdefault(sector, {})[f] = "judgment (estimate not stable)"
        report[sector] = {"index": label, "members": cols, "n_weeks": full["n"], "r2": round(full["r2"], 3),
                          "beta_nifty": round(full["beta"]["nifty"], 3), "factors": rows}
        measured[sector] = {f: rows[f] for f in MEASURED}

    meta_time = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sens["_meta"] = (f"Mixed provenance (updated {meta_time}). crude and usd_inr columns are MEASURED for the sectors in "
                     "_provenance marked 'measured' (weekly regression on Nifty, Brent, USD/INR over "
                     f"{args.years} years, shrunk toward judgment; see data/sector_sensitivity_report.json). Every other "
                     "value is a JUDGMENT placeholder, not measured. Keys starting with '_' are reserved and ignored as sectors.")
    sens["_provenance"] = provenance
    sens["_measured"] = measured

    print(f"{'sector':8} {'n':>4} {'R2':>5}  {'factor':8} {'beta':>8} {'se':>7} {'t':>6}  {'stable':6} -> value")
    for sector, r in report.items():
        if "factors" not in r:
            print(f"{sector:8} {r['status']}")
            continue
        for f, x in r["factors"].items():
            print(f"{sector:8} {r['n_weeks']:>4} {r['r2']:>5}  {f:8} {x['beta_ols']:>8} {x['se']:>7} {x['t']:>6}  "
                  f"{'yes' if x['stable'] else 'no':6} -> {sens[sector][f]} ({provenance[sector][f]})")
    if args.dry_run:
        return 0
    path.write_text(json.dumps(sens, indent=2) + "\n", encoding="utf-8")
    (DATA / "sector_sensitivity_report.json").write_text(json.dumps(
        {"generated": meta_time, "years": args.years, "model": "weekly log returns; r_sector ~ nifty + brent + usdinr",
         "shrink_k": SHRINK_K, "sectors": report}, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
