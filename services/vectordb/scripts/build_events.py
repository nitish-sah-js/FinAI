"""data/events_seed.csv → live yfinance outcomes (08 §5) → data/historical_events.json.

There is NO synthetic path: when prices are missing for an asset, that asset is skipped and logged (08 §5.7), and
the skip is recorded in the event's "outcomes_skipped". NSE sector indices without Yahoo history are computed from
a liquid proxy (vectordb.assets.PRICE_PROXIES) and the proxy is recorded in the outcome.

    cd services/vectordb
    ../../.venv/Scripts/python scripts/build_events.py            # network needed (yfinance)
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))       # services/vectordb → `import vectordb`

from vectordb.assets import PRICE_PROXIES, benchmark_for          # noqa: E402
from vectordb.embed import event_text                              # noqa: E402
from vectordb.paths import corpus_path, holdout_path, seed_path    # noqa: E402

logger = logging.getLogger("build_events")

EVENT_TYPES = {"hurricane", "cyclone", "monsoon_deficit", "heatwave", "rate_shock", "oil_shock", "policy"}
SPLITS = {"train", "holdout"}
HORIZONS = (1, 5, 20)
EST_START, EST_END, MIN_EST = 130, 10, 100          # estimation window [t0-130, t0-10] trading days, ≥100 returns
REQUIRED = ("event_id", "title", "event_type", "region", "country", "event_date", "severity_value", "severity_unit",
            "description", "mechanism", "tickers", "split")
_TICKER = re.compile(r"^\^?[A-Z0-9&.\-]+(=[A-Z])?$")


# ---------------------------------------------------------------- severity (08 §3.3)
def severity_norm(event_type: str, value: float) -> float:
    v = abs(float(value))
    div = {"hurricane": 5.0, "cyclone": 7.0, "monsoon_deficit": 30.0, "heatwave": 6.0, "rate_shock": 100.0,
           "oil_shock": 20.0}.get(event_type)
    return round(min(v / div if div else v, 1.0), 4)


# ---------------------------------------------------------------- seed validation
class SeedError(ValueError):
    pass


def _iso(s: str, field: str, eid: str) -> str:
    try:
        return date.fromisoformat(s.strip()).isoformat()
    except ValueError as e:
        raise SeedError(f"{eid}: bad {field} {s!r}") from e


def parse_seed(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        header = reader.fieldnames or []
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise SeedError(f"seed header lacks {missing}")
    out, seen = [], set()
    for n, r in enumerate(rows, start=2):
        if None in r:                                    # more fields than the header → unquoted comma
            raise SeedError(f"line {n}: {len(r[None])} extra field(s); quote fields that contain commas")
        r = {k: (v or "").strip() for k, v in r.items()}
        eid = r["event_id"]
        for c in REQUIRED:
            if not r.get(c):
                raise SeedError(f"line {n} ({eid}): empty {c}")
        if eid in seen:
            raise SeedError(f"duplicate event_id {eid}")
        seen.add(eid)
        if r["event_type"] not in EVENT_TYPES:
            raise SeedError(f"{eid}: event_type {r['event_type']!r} not in {sorted(EVENT_TYPES)}")
        if r["split"] not in SPLITS:
            raise SeedError(f"{eid}: split {r['split']!r} not in {sorted(SPLITS)}")
        try:
            sev = float(r["severity_value"])
        except ValueError as e:
            raise SeedError(f"{eid}: severity_value {r['severity_value']!r} is not a number") from e
        tickers = [t.strip() for t in r["tickers"].split("|") if t.strip()]
        bad = [t for t in tickers if not _TICKER.match(t)]
        if not tickers or bad:
            raise SeedError(f"{eid}: bad tickers {bad or r['tickers']!r}")
        ev = {
            "event_id": eid, "title": r["title"], "event_type": r["event_type"], "region": r["region"],
            "country": r["country"],
            "start_date": _iso(r.get("start_date") or r["event_date"], "start_date", eid),
            "end_date": _iso(r.get("end_date") or r["event_date"], "end_date", eid),
            "event_date": _iso(r["event_date"], "event_date", eid),
            "severity_value": sev, "severity_unit": r["severity_unit"],
            "severity_norm": severity_norm(r["event_type"], sev),
            "description": r["description"], "mechanism": r["mechanism"],
            "affected_assets": [a.strip() for a in r.get("affected_assets", "").split("|") if a.strip()],
            "tickers": tickers, "split": r["split"],
            "source": r.get("source", ""), "source_url": r.get("source_url", ""),
            "date_approximate": r.get("date_approximate", "").lower() == "true",
            "notes": r.get("notes", ""),
        }
        if not (ev["start_date"] <= ev["event_date"] <= ev["end_date"]) and ev["end_date"] >= ev["start_date"]:
            logger.warning("%s: event_date %s outside [%s, %s]", eid, ev["event_date"], ev["start_date"], ev["end_date"])
        ev["embedding_text"] = event_text(ev)
        out.append(ev)
    return out


def check_holdout_consistency(events: list[dict], holdout_file: Path) -> None:
    """Every holdout record of the backtest file must exist here with split=holdout and the same event_date."""
    if not holdout_file.exists():
        logger.warning("holdout file %s not found; consistency not checked", holdout_file)
        return
    recs = json.loads(holdout_file.read_text(encoding="utf-8"))
    by_id = {e["event_id"]: e for e in events}
    problems = []
    for r in recs:
        if r.get("split") != "holdout":
            continue
        e = by_id.get(r["event_id"])
        if e is None:
            problems.append(f"{r['event_id']} missing from the seed")
        elif e["split"] != "holdout":
            problems.append(f"{r['event_id']} is split={e['split']} in the seed")
        elif e["event_date"] != str(r.get("event_date"))[:10]:
            problems.append(f"{r['event_id']} event_date {e['event_date']} != backtest {r.get('event_date')}")
    if problems:
        raise SeedError("holdout mismatch with " + str(holdout_file) + ": " + "; ".join(problems))


# ---------------------------------------------------------------- outcome math (pure; tested on synthetic prices)
def compute_outcome(px: pd.Series, bench: pd.Series | None, event_date: date | str, *, asset: str,
                    benchmark: str) -> tuple[dict | None, str | None]:
    """08 §5: t0 = last close strictly BEFORE event_date; ret_k = P(t0+k)/P(t0) − 1 (k trading days);
    beta = OLS on daily returns over [t0−130, t0−10] (strictly pre-event); abnormal_k = ret_k − beta·bench_ret_k.
    asset == benchmark → abnormal None, beta 1. Returns (outcome, None) or (None, skip_reason)."""
    ed = pd.Timestamp(event_date if isinstance(event_date, str) else event_date.isoformat())
    self_bench = bench is None or asset == benchmark
    px = px.dropna().astype(float)
    px = px[~px.index.duplicated(keep="last")].sort_index()
    if self_bench:
        df = pd.DataFrame({"a": px, "b": px})
    else:
        b = bench.dropna().astype(float)
        b = b[~b.index.duplicated(keep="last")].sort_index()
        df = pd.concat({"a": px, "b": b}, axis=1, join="inner").dropna()
    if df.empty:
        return None, "no overlapping prices"
    idx = df.index.tz_localize(None) if getattr(df.index, "tz", None) is not None else df.index
    before = np.flatnonzero(idx < ed)
    if before.size == 0:
        return None, "no trading day before event_date"
    t0 = int(before[-1])
    if t0 + max(HORIZONS) >= len(df):
        return None, f"fewer than {max(HORIZONS)} trading days after event"
    if (ed - idx[t0]).days > 7:
        return None, f"last close before event is stale ({idx[t0].date()})"
    if t0 - EST_START < 0:
        return None, f"only {t0} trading days before t0 (need {EST_START})"
    a, bv = df["a"].to_numpy(), df["b"].to_numpy()
    window = slice(t0 - EST_START, t0 + max(HORIZONS) + 1)
    if (a[window] <= 0).any() or (bv[window] <= 0).any():
        return None, "non-positive price in window (percent returns undefined, e.g. WTI 2020-04-20)"
    est_a = a[t0 - EST_START: t0 - EST_END + 1]
    est_b = bv[t0 - EST_START: t0 - EST_END + 1]
    ra, rb = np.diff(est_a) / est_a[:-1], np.diff(est_b) / est_b[:-1]
    ok = np.isfinite(ra) & np.isfinite(rb)
    ra, rb = ra[ok], rb[ok]
    if ra.size < MIN_EST:
        return None, f"only {ra.size} estimation returns (need {MIN_EST})"
    if a[t0] == 0 or not np.isfinite(a[t0]):
        return None, "zero/invalid base price"
    if self_bench:
        beta = 1.0
    else:
        var_b = float(np.var(rb, ddof=1))
        beta = float(np.cov(ra, rb, ddof=1)[0, 1] / var_b) if var_b > 1e-12 else 1.0
    out: dict[str, Any] = {"asset": asset, "benchmark": benchmark, "beta_used": round(beta, 4),
                           "t0_date": idx[t0].date().isoformat(), "realized_dates": {}}
    for k in HORIZONS:
        r = float(a[t0 + k] / a[t0] - 1.0)
        rbk = float(bv[t0 + k] / bv[t0] - 1.0)
        if not (np.isfinite(r) and np.isfinite(rbk)):
            return None, f"non-finite {k}d return"
        out[f"ret_{k}d"] = round(r, 6)
        out[f"abnormal_{k}d"] = None if self_bench else round(r - beta * rbk, 6)
        out["realized_dates"][f"{k}d"] = idx[t0 + k].date().isoformat()
    out["est_window"] = [idx[t0 - EST_START].date().isoformat(), idx[t0 - EST_END].date().isoformat()]
    return out, None


# ---------------------------------------------------------------- prices
class PriceSource:
    """Full daily history per symbol from yfinance, downloaded once per run."""

    def __init__(self, start: str = "2004-01-01", end: str | None = None):
        self.start, self.end = start, end or (date.today() + timedelta(days=1)).isoformat()
        self._cache: dict[str, pd.Series | None] = {}

    def get(self, symbol: str) -> pd.Series | None:
        if symbol not in self._cache:
            self._cache[symbol] = self._download(symbol)
        return self._cache[symbol]

    def _download(self, symbol: str) -> pd.Series | None:
        import yfinance as yf
        try:
            df = yf.download(symbol, start=self.start, end=self.end, progress=False, auto_adjust=True, threads=False)
        except Exception as e:  # noqa: BLE001
            logger.warning("download %s failed: %s", symbol, e)
            return None
        if df is None or df.empty or "Close" not in df:
            logger.warning("no Yahoo history for %s", symbol)
            return None
        s = df["Close"]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        s = s.dropna()
        return s if len(s) else None


# ---------------------------------------------------------------- build
def build(seed: Path, out: Path, prices: PriceSource, holdout_file: Path | None = None) -> dict:
    if out.resolve() == holdout_path().resolve():
        raise SystemExit(f"refusing to write the corpus to {out}: that is the backtest holdout file")
    events = parse_seed(seed)
    check_holdout_consistency(events, holdout_file or holdout_path())
    stats = {"events": len(events), "outcomes": 0, "skipped": [], "proxies": []}
    for e in events:
        e["outcomes"], e["outcomes_skipped"] = [], []
        for t in e["tickers"]:
            sym = PRICE_PROXIES.get(t, t)
            bench = benchmark_for(t)
            px = prices.get(sym)
            if px is None:
                reason = f"no Yahoo history for {sym}"
                o = None
            else:
                bpx = None if bench == sym else prices.get(bench)
                if bench != sym and bpx is None:
                    o, reason = None, f"no Yahoo history for benchmark {bench}"
                else:
                    o, reason = compute_outcome(px, bpx, e["event_date"], asset=t if sym == t else sym,
                                                benchmark=bench)
            if o is None:
                logger.info("SKIP %-26s %-14s %s", e["event_id"], t, reason)
                e["outcomes_skipped"].append({"asset": t, "price_symbol": sym, "reason": reason})
                stats["skipped"].append(f"{e['event_id']}:{t} ({reason})")
                continue
            o["asset"] = t
            o["price_symbol"] = sym
            o["price_source"] = "yfinance (auto_adjust close)"
            if sym != t:
                o["proxy_note"] = f"{t} has no Yahoo history; computed from {sym}"
                stats["proxies"].append(f"{e['event_id']}:{t}->{sym}")
            e["outcomes"].append(o)
            stats["outcomes"] += 1
        e["outcomes_json"] = json.dumps(e["outcomes"])
    payload = events
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)
    stats["built_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    logger.info("wrote %d events, %d outcomes (%d skipped, %d via proxy) to %s", len(events), stats["outcomes"],
                len(stats["skipped"]), len(stats["proxies"]), out)
    return stats


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    stats = build(a.seed or seed_path(), a.out or corpus_path(), PriceSource())
    print(json.dumps({k: v for k, v in stats.items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
