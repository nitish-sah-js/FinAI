"""Typed async HTTP calls to the L2/L3 services. Every call returns a ToolResult and never raises (04 §9 step 4)."""
from __future__ import annotations

import asyncio
import copy
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any

import httpx

from copilot_common import reachability
from copilot_common.ids import counter_for
from copilot_common.models import Evidence, ToolResult
from copilot_common.service_base import load_fixture, now_utc
from copilot_common.settings import get_settings

# tool → (fixture/service dir, Settings URL attribute, endpoint)
ENDPOINTS: dict[str, tuple[str, str, str]] = {
    "news": ("ingestion", "INGEST_URL", "/news"),
    "weather": ("ingestion", "INGEST_URL", "/weather/features"),
    "macro": ("ingestion", "INGEST_URL", "/macro/features"),
    "prices": ("ingestion", "INGEST_URL", "/prices"),
    "sentiment": ("sentiment", "SENTIMENT_URL", "/sentiment/score"),
    "agri": ("agri", "AGRI_URL", "/agri_signal"),
    "analogs": ("vectordb", "VECTOR_URL", "/find_analogs"),
    "exposure": ("quant", "QUANT_URL", "/exposure"),
    "risk": ("quant", "QUANT_URL", "/var_montecarlo"),
    "hedge": ("quant", "QUANT_URL", "/hedge_proposals"),
    "event_study": ("quant", "QUANT_URL", "/event_study"),
    "scenario": ("quant", "QUANT_URL", "/scenario"),
    "correlations": ("quant", "QUANT_URL", "/correlations"),
    "scenario_evidence": ("quant", "QUANT_URL", "/scenario/from_evidence"),   # hedge review phase 1
    "hedge_validation": ("quant", "QUANT_URL", "/hedge_validation"),          # hedge review phase 2 (+ gated optimizer)
}

# chaos flag → tool it breaks (simulated here too, so the demo works even when services are mocked)
CHAOS_TOOL = {"weather_down": "weather", "vector_down": "analogs", "agri_raster_missing": "agri"}

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=20, limits=httpx.Limits(max_connections=50))
    return _client


def _as_of_cutoff(as_of: Any) -> datetime | None:
    if not as_of:
        return None
    d = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)[:10])
    return datetime.combine(d, dtime(23, 59, 59), tzinfo=timezone.utc)


def _fixture_result(service: str, endpoint: str, tool: str) -> ToolResult:
    try:
        return ToolResult.model_validate(load_fixture(service, endpoint))
    except (OSError, ValueError):
        t = now_utc()
        return ToolResult(evidence=[Evidence(id=f"ev_{tool}_000", tool=tool, value={}, source="none", as_of=t, timestamp=t,
                                             confidence=0.1, degraded=True, degraded_reason="no_fixture",
                                             summary=f"{tool}: no data")])


def _retime_fixture(tr: ToolResult, cutoff: datetime | None) -> None:
    """Fixtures carry fixed dates; shift them so freshness is realistic (and ≤ as_of in time-machine mode)."""
    anchor = cutoff or now_utc()
    for ev in tr.evidence:
        ev.timestamp = anchor
        ev.as_of = anchor - timedelta(seconds=ev.freshness_s or 0)


def _renumber(tr: ToolResult, run_id: str | None) -> None:
    """Evidence ids are unique per run: ev_<tool>_<nnn> from the run's counter."""
    if not run_id:
        return
    counter = counter_for(run_id)
    mapping: dict[str, str] = {}
    for ev in tr.evidence:
        new = counter.next(ev.tool)
        mapping[ev.id] = new
        ev.id = new
        ev.run_id = run_id
    for ev in tr.evidence:            # patch nested references (e.g. hedge proposals)
        for p in ev.value.get("proposals", []) if isinstance(ev.value.get("proposals"), list) else []:
            if isinstance(p, dict) and "evidence_ids" in p:
                p["evidence_ids"] = [mapping.get(i, i) for i in p["evidence_ids"]]


def _degrade(tr: ToolResult, reason: str, factor: float = 0.5) -> ToolResult:
    for ev in tr.evidence:
        ev.degraded = True
        ev.degraded_reason = reason
        if ev.confidence is not None:
            ev.confidence = round(ev.confidence * factor, 3)
        ev.summary = f"{ev.summary or ev.tool} (degraded: {reason})"
    return tr


async def call_tool(tool: str, body: dict, *, run_id: str | None = None, chaos: dict | None = None,
                    timeout_s: float = 15.0) -> ToolResult:
    s = get_settings()
    service, url_attr, endpoint = ENDPOINTS[tool]
    chaos = chaos or {}
    cutoff = _as_of_cutoff(body.get("as_of"))
    body = {**body, "run_id": run_id, "chaos": chaos}
    if chaos.get("slow_network_ms"):
        await asyncio.sleep(chaos["slow_network_ms"] / 1000)

    broken = next((flag for flag, t in CHAOS_TOOL.items() if t == tool and chaos.get(flag)), None)
    if broken:
        tr = _fixture_result(service, endpoint, tool)
        _retime_fixture(tr, cutoff)
        for ev in tr.evidence:
            ev.as_of -= timedelta(hours=6)                      # "cached value from 6h ago"
            ev.freshness_s = (ev.freshness_s or 0) + 21_600
        tr.warnings.append(f"{broken} chaos flag set")
        _degrade(tr, "chaos")
    elif s.MOCK:
        await asyncio.sleep(s.MOCK_DELAY_MS / 1000)
        tr = _fixture_result(service, endpoint, tool)
        _retime_fixture(tr, cutoff)
        for ev in tr.evidence:
            ev.degraded_reason = ev.degraded_reason or "mock"
    else:
        url = getattr(s, url_attr).rstrip("/") + endpoint
        headers = {"X-Run-Id": run_id or ""}
        active = [k for k, v in chaos.items() if v]
        if active:
            headers["X-Chaos"] = ",".join(active)
        try:
            if reachability.is_down(url):
                raise httpx.ConnectError(f"{reachability.host_key(url)} marked down (retry in ≤30 s)")
            r = await client().post(url, json=body, headers=headers,
                                    timeout=httpx.Timeout(timeout_s, connect=reachability.CONNECT_TIMEOUT_S))
            reachability.mark_up(url)
            r.raise_for_status()
            tr = ToolResult.model_validate(r.json())
        except (httpx.HTTPError, ValueError) as e:
            if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
                reachability.mark_down(url)
            tr = _fixture_result(service, endpoint, tool)
            _retime_fixture(tr, cutoff)
            tr.warnings.append(f"{tool}: {type(e).__name__} calling {url}")
            _degrade(tr, "service_unreachable")

    # time-machine guard: nothing may be dated after as_of (04 §5)
    if cutoff:
        for ev in tr.evidence:
            if ev.as_of > cutoff:
                ev.degraded, ev.degraded_reason = True, "lookahead_violation"
                ev.confidence = 0.0
                tr.warnings.append(f"{ev.id}: data after as_of")
    tr = copy.deepcopy(tr)
    _renumber(tr, run_id)
    return tr


# ---------- typed helpers ----------
async def news(query: str, tickers: list[str], as_of=None, **kw) -> ToolResult:
    return await call_tool("news", {"query": query, "tickers": tickers, "since_hours": 48, "limit": 30, "as_of": as_of}, **kw)


async def weather(region_id: str, horizon_days: int = 5, as_of=None, **kw) -> ToolResult:
    return await call_tool("weather", {"region_id": region_id, "horizon_days": horizon_days, "as_of": as_of}, **kw)


async def macro(as_of=None, **kw) -> ToolResult:
    return await call_tool("macro", {"as_of": as_of}, **kw)


async def sentiment(items: list[dict], portfolio: dict, as_of=None, **kw) -> ToolResult:
    return await call_tool("sentiment", {"items": items, "portfolio": portfolio, "second_opinion": True, "as_of": as_of}, **kw)


async def agri(region_id: str, on_date: str, crop_season: str | None = None, as_of=None, **kw) -> ToolResult:
    return await call_tool("agri", {"region_id": region_id, "date": on_date, "crop_season": crop_season, "as_of": as_of}, **kw)


async def analogs(situation: str, event_type: str | None, region_hint: str | None, assets: list[str],
                  horizon: str = "5d", as_of=None, exclude_holdout: bool = False, **kw) -> ToolResult:
    return await call_tool("analogs", {"situation": situation, "event_type": event_type, "region_hint": region_hint,
                                       "assets": assets, "horizon": horizon, "k": 5, "alpha": 0.6,
                                       "exclude_holdout": exclude_holdout, "as_of": as_of}, **kw)


async def exposure(portfolio: dict, as_of=None, **kw) -> ToolResult:
    return await call_tool("exposure", {"portfolio": portfolio, "benchmark": "^NSEI", "as_of": as_of}, **kw)


async def risk(portfolio: dict, horizon_days: int = 5, as_of=None, **kw) -> ToolResult:
    return await call_tool("risk", {"portfolio": portfolio, "method": "montecarlo", "horizon_days": horizon_days,
                                    "confidence_level": 0.95, "as_of": as_of}, **kw)


async def hedge(portfolio: dict, horizon_days: int = 5, as_of=None, **kw) -> ToolResult:
    return await call_tool("hedge", {"portfolio": portfolio, "target": "min_variance", "horizon_days": horizon_days,
                                     "as_of": as_of}, **kw)


async def scenario(portfolio: dict, shocks: dict, as_of=None, **kw) -> ToolResult:
    return await call_tool("scenario", {"portfolio": portfolio, "shocks": shocks, "as_of": as_of}, **kw)


async def scenario_from_evidence(portfolio: dict, evidence: list[dict], horizon: str = "5d", as_of=None,
                                 **kw) -> ToolResult:
    return await call_tool("scenario_evidence", {"portfolio": portfolio, "evidence": evidence, "horizon": horizon,
                                                 "as_of": as_of}, **kw)


async def hedge_validation(portfolio: dict, events: list[dict], horizon_days: int = 5, as_of=None,
                           **kw) -> ToolResult:
    return await call_tool("hedge_validation", {"portfolio": portfolio, "events": events, "horizon_days": horizon_days,
                                                "as_of": as_of}, **kw)


async def event_study(ticker: str, event_date: str, as_of=None, **kw) -> ToolResult:
    return await call_tool("event_study", {"ticker": ticker, "event_date": event_date, "event_window": [-1, 5],
                                           "as_of": as_of}, **kw)
