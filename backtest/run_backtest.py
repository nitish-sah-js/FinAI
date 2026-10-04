"""CLI: python -m backtest.run_backtest --events data/events.json --split holdout --mode local --cache replay

Start the orchestrator (and whatever services exist) with the same CACHE_MODE / LLM_MODE first: those are read by
each service from its own env (01 §3), not sent in the QueryRequest.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
from datetime import date, timedelta
from pathlib import Path

import httpx

from . import baselines, metrics
from .calibration import calibration_bins
from .client import run_case
from .events import BacktestCase, load_cases
from .extract import check_leakage, extract_prediction, holdout_analogs, similarity_of_top_analog
from .realized import fetch_bars, realized_return



def case_portfolio(case: BacktestCase) -> dict:
    """The question is asked for a portfolio holding exactly the assets the case scores (1 unit each). With the demo
    portfolio instead, the copilot analysed RELIANCE/ONGC/... while the scoreboard scored NG=F, ADANIPORTS, ..., so
    13 of 16 points had no prediction at all. Quantity does not enter the per-asset return forecast."""
    return {"portfolio_id": f"bt_{case.event_id}", "holdings": [{"ticker": a, "qty": 1} for a in case.assets]}


def fetch_sentiment(case: BacktestCase, asset: str) -> float | None:
    """News tone for the sentiment_only baseline: ingestion POST /news (GDELT ≤ as_of, 10) → sentiment POST
    /sentiment/score (07). The sentiment service needs the headlines themselves (items), so the news has to be
    fetched first. Returns None (no direction) when either service is down, which the scoreboard reports."""
    from copilot_common.settings import get_settings
    s = get_settings()
    try:
        with httpx.Client(timeout=60) as c:
            news = c.post(f"{s.INGEST_URL}/news", json={"query": case.query, "tickers": [asset], "since_hours": 72,
                                                        "limit": 30, "as_of": case.as_of})
            news.raise_for_status()
            items = next((e["value"].get("items", []) for e in news.json().get("evidence", []) if e.get("tool") == "news"), [])
            if not items:
                return None
            r = c.post(f"{s.SENTIMENT_URL}/sentiment/score",
                       json={"items": items, "as_of": case.as_of, "second_opinion": False})
            r.raise_for_status()
            ev = r.json()["evidence"]
            if not ev:
                return None
            v = ev[0]["value"]
            return float(v.get("by_ticker", {}).get(asset, v.get("portfolio_sentiment", 0.0)))
    except (httpx.HTTPError, ValueError, KeyError):
        return None


def _summ(points: list[dict], key: str, mae_zero: float | None) -> dict:
    pdir = [p[key]["direction"] for p in points]
    real = [p["realized"] for p in points]
    med = [p[key]["median"] for p in points]
    hr, n_dir = metrics.hit_rate(pdir, real)
    # n = points scored (MAE, coverage); n_directional = points with a non-zero call (hit rate, Brier). 12 §A8 shows
    # zero with "n": 14, so n must not be the directional count (that gave zero/sentiment_only n = 0).
    out = {"hit_rate": hr, "mae": metrics.mae(med, real) if points else None,
           "coverage_80": metrics.coverage_80([p[key]["p10"] for p in points], [p[key]["p90"] for p in points], real) if points else None,
           "n": len(points), "n_directional": n_dir}
    if key != "zero":
        m = [d != 0 for d in pdir]
        nz = [r for r, k in zip(real, m) if k]
        out["hit_rate_ci"] = metrics.bootstrap_ci([1.0 if d == (1 if r > 0 else -1) else 0.0
                                                   for d, r in zip(pdir, real) if d != 0]) if nz else None
        out["skill_vs_zero"] = metrics.skill_vs_zero(out["mae"], mae_zero)
    return out


def build_scoreboard(points: list[dict], cfg: dict) -> dict:
    mae_zero = metrics.mae([0] * len(points), [p["realized"] for p in points]) if points else None
    cop = [p for p in points if p.get("copilot")]
    methods = {}
    if cop:
        c = _summ(cop, "copilot", metrics.mae([0] * len(cop), [p["realized"] for p in cop]))
        c["brier"] = metrics.brier([p["copilot"]["confidence"] for p in cop], [p["copilot"]["direction"] for p in cop],
                                   [p["realized"] for p in cop])
        c["mae_ci"] = metrics.bootstrap_ci([abs(p["copilot"]["median"] - p["realized"]) for p in cop])
        methods["copilot"] = c
    for k in ("price_only", "sentiment_only"):
        s = _summ(points, k, mae_zero)
        s["brier"] = None
        methods[k] = s
    methods["zero"] = {k: v for k, v in _summ(points, "zero", mae_zero).items() if k in ("hit_rate", "mae", "coverage_80", "n")}
    rows, misses = [], []
    for p in points:
        c = p.get("copilot")
        hit = bool(c and c["direction"] != 0 and c["direction"] == (1 if p["realized"] > 0 else -1 if p["realized"] < 0 else 0))
        inint = bool(c and c["p10"] is not None and c["p10"] <= p["realized"] <= c["p90"])
        rows.append({"event_id": p["event_id"], "asset": p["asset"], "as_of": p["as_of"], "run_id": p["run_id"],
                     "copilot": ({k: c[k] for k in ("median", "p10", "p90", "direction", "confidence")} if c else None),
                     "price_only": {k: p["price_only"][k] for k in ("median", "direction")},
                     "sentiment_only": {k: p["sentiment_only"][k] for k in ("median", "direction")},
                     "realized": p["realized"], "hit": hit, "in_interval": inint})
        if c and c["direction"] != 0 and not hit:
            sim = p.get("top_similarity")
            misses.append({"event_id": p["event_id"], "asset": p["asset"], "pred_median": c["median"],
                           "realized": p["realized"],
                           "why_missed": "PENDING HUMAN REVIEW (read the trace) — " +
                                         (f"top analog similarity {sim:.2f}." if sim is not None else "analog similarity n/a."),
                           "run_id": p["run_id"]})
    return {"generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "config": cfg,
            "methods": methods, "rows": rows,
            "calibration": calibration_bins([p["copilot"]["confidence"] for p in cop], [p["copilot"]["direction"] for p in cop],
                                            [p["realized"] for p in cop]),
            "misses": misses,
            "disclaimer": f"{cfg['n_events']} events, {cfg['n_points']} points. Small sample; decision support, not trading signals."}


async def collect(cases: list[BacktestCase], llm_mode: str, holdout_ids: set[str] | None = None) -> tuple[list[dict], dict]:
    points, notes = [], {"no_prediction": [], "no_realized": [], "sentiment_unavailable": 0, "degraded_runs": []}
    for case in cases:  # sequential: GPUs are shared
        run_id, final, events = await run_case(case, llm_mode=llm_mode, portfolio=case_portfolio(case))
        print(f"[run] {case.event_id} -> {run_id}")
        bad = check_leakage(final, events, case.as_of)
        bad += [f"holdout analog returned: {e}" for e in holdout_analogs(final, (holdout_ids or set()) - {case.event_id})]
        bad += [f"the scored event itself was returned as an analog: {case.event_id}"
                for _ in holdout_analogs(final, {case.event_id})]
        if bad:
            raise RuntimeError(f"LEAKAGE in {case.event_id}/{run_id}: {bad}")
        if any(e.get("degraded") for e in final.get("evidence", [])):
            notes["degraded_runs"].append(run_id)
        horizon = f"{case.horizon_days}d"
        sim = similarity_of_top_analog(final, events)
        d = date.fromisoformat(case.as_of)
        for asset in case.assets:
            real = await asyncio.to_thread(realized_return, asset, case.as_of, case.horizon_days)
            if real is None:
                print(f"[skip] no realized return for {asset}")
                notes["no_realized"].append(f"{case.event_id}:{asset}")
                continue
            hist = await asyncio.to_thread(fetch_bars, asset, (d - timedelta(days=420)).isoformat(), case.as_of, case.as_of)
            sent = await asyncio.to_thread(fetch_sentiment, case, asset)
            if sent is None:
                notes["sentiment_unavailable"] += 1
            pred = extract_prediction(final, asset, horizon, events)
            if pred is None:
                notes["no_prediction"].append(f"{case.event_id}:{asset}")
            points.append({"event_id": case.event_id, "asset": asset, "as_of": case.as_of, "run_id": run_id,
                           "copilot": pred, "realized": real,
                           "price_only": baselines.price_only(hist, case.as_of, case.horizon_days),
                           "sentiment_only": baselines.sentiment_only(hist, case.as_of, sent, case.horizon_days),
                           "zero": baselines.zero(hist, case.as_of, case.horizon_days), "top_similarity": sim})
    return points, notes


def main(argv=None):
    from copilot_common.settings import get_settings, reload_settings
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default=None, help="default: <data>/events.json")
    ap.add_argument("--split", default="holdout")
    ap.add_argument("--mode", default="local")
    ap.add_argument("--cache", default="replay")
    ap.add_argument("--horizon", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None, help="only the first N events (smoke test)")
    ap.add_argument("--out", default=None, help="default: <data>/backtest/scoreboard.json")
    a = ap.parse_args(argv)
    # CACHE_MODE also governs this process's price cache (copilot_common.prices).
    os.environ["CACHE_MODE"] = a.cache
    s = reload_settings()
    events_path = a.events or str(s.data_dir / "events.json")
    out_path = Path(a.out or s.data_dir / "backtest" / "scoreboard.json")
    print(f"NOTE: start the orchestrator/services with CACHE_MODE={a.cache} and LLM_MODE={a.mode}; this flag only affects this process.")
    cases = load_cases(events_path, a.split, a.horizon)[: a.limit]
    holdout_ids = {c.event_id for c in load_cases(events_path, "holdout")} if a.split == "holdout" else set()
    points, notes = asyncio.run(collect(cases, a.mode, holdout_ids))
    cfg = {"llm_mode": a.mode, "horizon_days": a.horizon or 5, "split": a.split,
           "n_events": len(cases), "n_points": len(points), "orch_url": get_settings().ORCH_URL,
           "notes": {k: v for k, v in notes.items() if v}}
    sb = build_scoreboard(points, cfg)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(sb, indent=2), encoding="utf-8")
    try:
        from rich.console import Console
        from rich.table import Table
        t = Table(title="Backtest scoreboard")
        for col in ("method", "hit_rate", "mae", "coverage_80", "brier", "skill_vs_zero", "n"):
            t.add_column(col)
        for name, m in sb["methods"].items():
            t.add_row(name, *(("-" if m.get(c) is None else f"{m[c]:.3f}" if isinstance(m.get(c), float) else str(m.get(c, "-")))
                              for c in ("hit_rate", "mae", "coverage_80", "brier", "skill_vs_zero", "n")))
        Console().print(t)
    except ImportError:
        print(json.dumps(sb["methods"], indent=2))
    if cfg["notes"]:
        print("notes:", json.dumps(cfg["notes"]))
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
