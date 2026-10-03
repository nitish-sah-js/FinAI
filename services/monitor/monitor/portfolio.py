"""Holdings, weights and region links (11 §2)."""
from __future__ import annotations

import csv
import json

import httpx

from copilot_common import reachability
from copilot_common.settings import get_data_dir, get_settings


def load_csv(name: str = "portfolio_demo.csv") -> list[dict]:
    p = get_data_dir() / name
    with p.open(encoding="utf-8") as f:
        return [{"ticker": r["ticker"].strip(), "qty": float(r["qty"]), "avg_price": float(r.get("avg_price") or 0),
                 "sector": (r.get("sector") or "").strip() or None} for r in csv.DictReader(f) if r.get("ticker")]


async def load_portfolio(portfolio_id: str = "demo") -> tuple[list[dict], str]:
    """Orchestrator GET /portfolio/{id} when reachable, else data/portfolio_demo.csv. Returns (holdings, source)."""
    url = get_settings().ORCH_URL.rstrip("/") + f"/portfolio/{portfolio_id}"
    if not reachability.is_down(url):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=reachability.CONNECT_TIMEOUT_S)) as c:
                r = await c.get(url)
            reachability.mark_up(url)
            if r.status_code == 200:
                return [{"ticker": h["ticker"], "qty": float(h["qty"]), "avg_price": float(h.get("avg_price") or 0),
                         "sector": h.get("sector")} for h in r.json().get("holdings", [])], "orchestrator"
        except httpx.HTTPError as e:
            if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
                reachability.mark_down(url)
    return load_csv(), "csv"


def weights(holdings: list[dict], last_price: dict[str, float]) -> dict[str, float]:
    """w_i = qty_i·price_i / Σ qty_j·price_j; price = last bar close, else avg_price."""
    vals = {h["ticker"]: h["qty"] * (last_price.get(h["ticker"]) or h.get("avg_price") or 0.0) for h in holdings}
    total = sum(vals.values())
    return {t: v / total for t, v in vals.items()} if total > 0 else {}


def region_links(name: str = "region_exposure.json") -> dict[str, list[dict]]:
    """region_id → [{ticker, strength, ...}]. Accepts the 09 §9 format ({"regions": {r: {"equities": [...]}}})
    and the 11 §13 format ({r: [{ticker, strength, crop}]})."""
    p = get_data_dir() / name
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    regions = raw.get("regions", raw)
    out: dict[str, list[dict]] = {}
    for rid, v in regions.items():
        if rid.startswith("_") or rid == "sector_defaults":
            continue
        eq = v.get("equities", []) if isinstance(v, dict) else v
        out[rid] = [{"ticker": e["ticker"], "strength": float(e.get("strength", 0.5)),
                     **{k: e[k] for k in ("link", "sign", "crop") if k in e}} for e in eq if isinstance(e, dict)]
    return out


def regions_for(held: set[str], links: dict[str, list[dict]]) -> list[str]:
    return [r for r, eq in links.items() if any(e["ticker"] in held for e in eq)]
