"""Held-out backtest cases from data/events.json (12 §A1; the file is owned by 08).

A record is a HistoricalEvent (08 §3) with "split". Backtest fields come from its optional "backtest" block
({"as_of", "assets", "query", "horizon_days"}); without it they are derived (as_of = event_date − 1 day,
assets = affected_assets).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path


@dataclass
class BacktestCase:
    event_id: str
    t0: str                      # event_date (day zero)
    as_of: str                   # no data after this date reaches the system
    assets: list[str]
    query: str
    horizon_days: int = 5
    title: str = ""
    meta: dict = field(default_factory=dict)


def load_cases(path: str | Path, split: str = "holdout", horizon: int | None = None) -> list[BacktestCase]:
    records = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(records, dict):
        records = records.get("events", [])
    out = []
    for r in records:
        if r.get("split") != split:
            continue
        bt = r.get("backtest") or {}
        t0 = str(r.get("event_date") or r.get("start_date"))[:10]
        as_of = str(bt.get("as_of") or (date.fromisoformat(t0) - timedelta(days=1)).isoformat())[:10]
        if as_of > t0:
            raise ValueError(f"{r['event_id']}: as_of {as_of} is after the event date {t0}")
        out.append(BacktestCase(
            event_id=r["event_id"], t0=t0, as_of=as_of,
            assets=list(bt.get("assets") or r.get("affected_assets") or []),
            query=bt.get("query") or f"{r.get('title', r['event_id'])}. Impact on my portfolio?",
            horizon_days=int(horizon or bt.get("horizon_days") or 5), title=r.get("title", ""),
            meta={k: r.get(k) for k in ("event_type", "region", "severity_value", "severity_unit")}))
    if not out:
        raise ValueError(f"no events with split={split!r} in {path}")
    return out
