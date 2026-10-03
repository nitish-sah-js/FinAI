"""CLI: python -m backtest.run_backtest --events data/events.json --split holdout --mode local --cache replay"""
from __future__ import annotations
import argparse, asyncio, json, os, datetime as dt
from pathlib import Path

from . import baselines, metrics
from .calibration import calibration_bins
from .client import run_case
from .events import BacktestCase, load_cases
from .extract import check_leakage, extract_prediction, similarity_of_top_analog
from .realized import fetch_bars, realized_return

SENTIMENT_URL = os.getenv("SENTIMENT_URL", "http://localhost:8102")
DEMO_PORTFOLIO = None  # orchestrator falls back to its demo portfolio when omitted


def fetch_sentiment(asset: str, as_of: str) -> float:
    """POST /sentiment/score (ToolResult). Uses by_ticker[asset], else portfolio_sentiment; 0.0 (no direction) on failure.
    The request shape is not fixed in 01 — confirm against 07 (assumed: tickers + as_of; 07 pulls GDELT <= as_of)."""
    import httpx
    try:
        r = httpx.post(f"{SENTIMENT_URL}/sentiment/score", json={"tickers": [asset], "as_of": as_of}, timeout=60)
        r.raise_for_status()
        v = r.json()["evidence"][0]["value"]
        return float(v.get("by_ticker", {}).get(asset, v.get("portfolio_sentiment", 0.0)))
    except Exception:
        return 0.0


def _summ(points: list[dict], key: str, mae_zero: float | None) -> dict:
    pdir = [p[key]["direction"] for p in points]
    real = [p["realized"] for p in points]
    med = [p[key]["median"] for p in points]
    hr, n = metrics.hit_rate(pdir, real)
    out = {"hit_rate": hr, "mae": metrics.mae(med, real) if points else None,
           "coverage_80": metrics.coverage_80([p[key]["p10"] for p in points], [p[key]["p90"] for p in points], real) if points else None,
           "n": n}
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


async def collect(cases: list[BacktestCase], llm_mode: str) -> list[dict]:
    from datetime import date, timedelta
    points = []
    for case in cases:  # sequential: GPUs are shared
        run_id, final, events = await run_case(case, llm_mode=llm_mode, portfolio=DEMO_PORTFOLIO)
        print(f"[run] {case.event_id} -> {run_id}")
        bad = check_leakage(final, events, case.as_of)
        if bad:
            raise RuntimeError(f"LEAKAGE in {case.event_id}/{run_id}: {bad}")
        horizon = f"{case.horizon_days}d"
        sim = similarity_of_top_analog(final, events)
        d = date.fromisoformat(case.as_of)
        for asset in case.assets:
            real = realized_return(asset, case.as_of, case.horizon_days)
            if real is None:
                print(f"[skip] no realized return for {asset}")
                continue
            hist = fetch_bars(asset, (d - timedelta(days=420)).isoformat(), case.as_of, as_of=case.as_of)
            sent = fetch_sentiment(asset, case.as_of)
            points.append({"event_id": case.event_id, "asset": asset, "as_of": case.as_of, "run_id": run_id,
                           "copilot": extract_prediction(final, asset, horizon, events), "realized": real,
                           "price_only": baselines.price_only(hist, case.as_of, case.horizon_days),
                           "sentiment_only": baselines.sentiment_only(hist, case.as_of, sent, case.horizon_days),
                           "zero": baselines.zero(hist, case.as_of, case.horizon_days), "top_similarity": sim})
    return points


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default="data/events.json")
    ap.add_argument("--split", default="holdout")
    ap.add_argument("--mode", default="local")
    ap.add_argument("--cache", default="replay")
    ap.add_argument("--horizon", type=int, default=None)
    ap.add_argument("--out", default="data/backtest/scoreboard.json")
    a = ap.parse_args(argv)
    # CACHE_MODE is read by each SERVICE from its own env (01 §3); QueryRequest has no such field.
    os.environ["CACHE_MODE"] = a.cache
    print(f"NOTE: start the orchestrator/services with CACHE_MODE={a.cache} and LLM_MODE={a.mode}; this flag only affects this process.")
    cases = load_cases(a.events, a.split, a.horizon)
    points = asyncio.run(collect(cases, a.mode))
    cfg = {"llm_mode": a.mode, "horizon_days": a.horizon or 5, "split": a.split,
           "n_events": len(cases), "n_points": len(points)}
    sb = build_scoreboard(points, cfg)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(sb, indent=2))
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
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
