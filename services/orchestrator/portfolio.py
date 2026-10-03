"""Portfolio load / save / CSV upload. Stored as data/portfolios/<id>.json; seeded from data/portfolio_demo.csv."""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from copilot_common.models import Holding, Portfolio
from copilot_common.settings import get_settings

DEMO_HOLDINGS = [
    ("RELIANCE.NS", 50, 2850, "Energy"), ("ONGC.NS", 600, 255, "Oil&Gas"), ("COALINDIA.NS", 700, 410, "Energy"),
    ("NTPC.NS", 500, 355, "Utilities"), ("ITC.NS", 450, 430, "FMCG"), ("HINDUNILVR.NS", 50, 2450, "FMCG"),
    ("HDFCBANK.NS", 120, 1850, "Banks"), ("UPL.NS", 230, 650, "Agri"),
]


def _dir() -> Path:
    d = get_settings().data_dir / "portfolios"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_id(pid: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", pid)[:64] or "demo"


def parse_csv(text: str, portfolio_id: str = "demo") -> tuple[Portfolio, list[str]]:
    """Columns ticker,qty[,avg_price,sector]. Tickers without a suffix get '.NS' (with a warning)."""
    warnings: list[str] = []
    reader = csv.DictReader(io.StringIO(text.strip()))
    holdings = []
    for i, row in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        t = row.get("ticker", "").upper()
        if not t:
            continue
        if "." not in t and not t.startswith("^") and "=" not in t:
            warnings.append(f"line {i}: {t} → {t}.NS")
            t += ".NS"
        try:
            qty = float(row.get("qty") or 0)
        except ValueError:
            warnings.append(f"line {i}: bad qty, skipped")
            continue
        avg = row.get("avg_price")
        holdings.append(Holding(ticker=t, qty=qty, avg_price=float(avg) if avg else None, sector=row.get("sector") or None))
    return Portfolio(portfolio_id=portfolio_id, holdings=holdings), warnings


def ensure_demo_csv() -> Path:
    p = get_settings().data_dir / "portfolio_demo.csv"
    if not p.is_file():
        lines = ["ticker,qty,avg_price,sector"] + [f"{t},{q},{a},{s}" for t, q, a, s in DEMO_HOLDINGS]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def load(portfolio_id: str = "demo") -> Portfolio:
    pid = _safe_id(portfolio_id)
    p = _dir() / f"{pid}.json"
    if p.is_file():
        return Portfolio.model_validate_json(p.read_text(encoding="utf-8"))
    if pid == "demo":
        return parse_csv(ensure_demo_csv().read_text(encoding="utf-8"))[0]
    raise KeyError(portfolio_id)


def save(portfolio: Portfolio) -> Portfolio:
    portfolio.portfolio_id = _safe_id(portfolio.portfolio_id)
    (_dir() / f"{portfolio.portfolio_id}.json").write_text(portfolio.model_dump_json(indent=1), encoding="utf-8")
    return portfolio


def summary(portfolio: dict) -> dict:
    """Compact portfolio view for prompts (saves tokens)."""
    hs = portfolio.get("holdings", [])
    return {"n_holdings": len(hs), "holdings": [{"ticker": h["ticker"], "sector": h.get("sector")} for h in hs],
            "currency": portfolio.get("currency", "INR")}


def to_json(portfolio: Portfolio) -> str:
    return json.dumps(portfolio.model_dump(mode="json"))
