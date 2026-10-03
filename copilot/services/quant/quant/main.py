from __future__ import annotations
import asyncio
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd
from fastapi import Header, HTTPException

from copilot_common.models import ToolResult
from copilot_common.service_base import create_service_app, degraded, mock_or

from . import correlations, event_study, exposure, hedges, options, risk, scenario
from .data import DataError, get_prices, latest_prices, load_json, lookback_start
from .evidence import make_evidence
from .schemas import (BsPriceReq, CorrReq, EventStudyReq, ExposureReq, HedgeReq, RiskReq, ScenarioReq)

app = create_service_app("quant")

FACTOR_TICKER = {"nifty": "^NSEI", "crude": "CL=F", "usd_inr": "INR=X", "us10y": "^TNX"}
FACTOR_LABEL = {"nifty": "Nifty", "crude": "Crude", "usd_inr": "USD/INR", "us10y": "US10Y", "monsoon_rain": "monsoon",
                "heatwave": "heatwave", "agri_stress": "agri stress", "repo_bps": "repo"}
BPS_FACTORS = {"repo_bps", "us10y"}


def _short(t: str) -> str:
    return t.replace(".NS", "").lstrip("^")


def _last_date(df: pd.DataFrame) -> date | None:
    return None if df is None or df.empty else df.index.max().date()


def _fail(tool: str, run_id: str | None, reason: str, t0: float, source: str = "quant") -> ToolResult:
    ev = degraded(run_id, tool, reason, value={"error": reason}, source=source,
                  summary=f"{tool} unavailable: {reason}")
    ev.latency_ms = int((time.perf_counter() - t0) * 1000)
    return ToolResult(evidence=[ev], warnings=[reason])


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def _deg_info(requested: list[str], df: pd.DataFrame, deg: set[str]) -> tuple[bool, str | None, list[str]]:
    missing = sorted(set(requested) - set(df.columns))
    bad = sorted(set(deg) | set(missing))
    if not bad:
        return False, None, []
    reason = "missing_data" if missing else "data_fallback"
    return True, reason, [f"{reason}: {', '.join(bad)}"]


def _need_holdings(portfolio) -> None:
    if not portfolio.holdings:
        raise HTTPException(422, "portfolio.holdings is empty")


# ================================================================ /event_study
async def _event_study(req: EventStudyReq, run_id: str | None) -> ToolResult:
    t0 = time.perf_counter()
    run_id = run_id or req.run_id
    lo = min(req.est_window[0], req.event_window[0])
    start = req.event_date - timedelta(days=int(abs(lo) * 1.6) + 20)
    end = req.event_date + timedelta(days=int(max(req.event_window[1], 0) * 1.8) + 12)
    tick = [req.ticker, req.benchmark]
    df, deg = await get_prices(tick, start, end, req.as_of)
    if req.ticker not in df.columns or req.benchmark not in df.columns:
        return _fail("event_study", run_id, f"missing price data for {req.ticker} or {req.benchmark}", t0)
    d, reason, warns = _deg_info(tick, df, deg)
    try:
        px = df[req.ticker].rename(req.ticker).dropna()
        res = await asyncio.to_thread(event_study.run, px, df[req.benchmark].dropna(), req.event_date,
                                      req.est_window, req.event_window)
    except DataError as e:
        return _fail("event_study", run_id, str(e), t0)
    res["ticker"] = req.ticker
    lo_w, hi_w = req.event_window
    summary = (f"{_short(req.ticker)} CAR({lo_w:+d},{hi_w:+d}) around {req.event_date.isoformat()} = "
               f"{res['car']:+.1%} (p={res['p_value']:.2f})")
    ev = make_evidence(run_id, "event_study", res, f"market model vs {req.benchmark}", _last_date(df),
                       0.8 if res["p_value"] < 0.05 else 0.5, d, reason, _ms(t0), summary)
    return ToolResult(evidence=[ev], warnings=warns)


# ================================================================ /correlations
async def _correlations(req: CorrReq, run_id: str | None) -> ToolResult:
    t0 = time.perf_counter()
    run_id = run_id or req.run_id
    if len(req.tickers) < 2:
        raise HTTPException(422, "need at least 2 tickers")
    end = req.as_of or date.today()
    df, deg = await get_prices(req.tickers, lookback_start(end, req.lookback_days + max(req.lags, default=0)), end, req.as_of)
    if df.shape[1] < 2:
        return _fail("correlations", run_id, "fewer than 2 tickers have price data", t0)
    d, reason, warns = _deg_info(req.tickers, df, deg)
    rets = np.log(df / df.shift(1)).iloc[1:].tail(req.lookback_days)
    res = await asyncio.to_thread(correlations.run, rets, req.lags, req.method)
    if not res["pairs"]:
        return _fail("correlations", run_id, "not enough overlapping data", t0)
    p0 = res["pairs"][0]
    same = [p for p in res["pairs"] if (p["a"], p["b"]) == (p0["a"], p0["b"])]
    txt = ", ".join(f"{p['corr']:.2f} {'same-day' if p['lag'] == 0 else 'at ' + str(p['lag']) + '-day lag'}" for p in same[:2])
    ev = make_evidence(run_id, "correlations", res, f"quant correlations_v1 ({req.method}, {req.lookback_days}d)", _last_date(df),
                       0.6, d, reason, _ms(t0), f"{_short(p0['a'])} vs {_short(p0['b'])}: corr {txt}")
    return ToolResult(evidence=[ev], warnings=warns)


# ================================================================ /var_montecarlo
async def _risk(req: RiskReq, run_id: str | None) -> ToolResult:
    t0 = time.perf_counter()
    run_id = run_id or req.run_id
    _need_holdings(req.portfolio)
    end = req.as_of or date.today()
    tick = [h.ticker for h in req.portfolio.holdings]
    df, deg = await get_prices(tick, lookback_start(end, req.lookback_days), end, req.as_of)
    held = [h for h in req.portfolio.holdings if h.ticker in df.columns]
    if not held:
        return _fail("risk", run_id, "no price data for any holding", t0)
    d, reason, warns = _deg_info(tick, df, deg)
    px = df[[h.ticker for h in held]].dropna().tail(req.lookback_days + 1)
    rets = px.pct_change().dropna()
    if len(rets) < 30:
        return _fail("risk", run_id, f"only {len(rets)} return observations", t0)
    last = latest_prices(px)
    vals = np.array([h.qty * last[h.ticker] for h in held])
    V = float(vals.sum())
    if V <= 0:
        return _fail("risk", run_id, "non-positive portfolio value", t0)
    w = vals / V
    h_, c = req.horizon_days, req.confidence_level
    if req.method == "historical":
        res = await asyncio.to_thread(risk.historical, rets, w, V, h_, c)
    elif req.method == "parametric":
        res = await asyncio.to_thread(risk.parametric, rets, w, V, h_, c)
    else:
        res = await asyncio.to_thread(risk.montecarlo, rets, w, V, h_, c, req.n_paths, req.seed)
    res["portfolio_value_inr"] = round(V, 2)
    label = {"montecarlo": "MC", "historical": "historical", "parametric": "parametric"}[req.method]
    conf = 0.75 if (req.lookback_days >= 250 and len(held) == len(req.portfolio.holdings)) else 0.5
    summary = f"{h_}d {c:.0%} {label} VaR ₹{res['var_inr']:,.0f} ({res['var_pct']:.1%} of portfolio)"
    ev = make_evidence(run_id, "risk", res, f"quant {label} on adj close, {req.lookback_days}d lookback", _last_date(df),
                       conf, d, reason, _ms(t0), summary)
    return ToolResult(evidence=[ev], warnings=warns)


# ================================================================ /scenario
async def _scenario(req: ScenarioReq, run_id: str | None) -> ToolResult:
    t0 = time.perf_counter()
    run_id = run_id or req.run_id
    _need_holdings(req.portfolio)
    bad = sorted(set(req.shocks) - scenario.SUPPORTED)
    if bad or not req.shocks:
        raise HTTPException(422, f"unsupported or empty shocks: {bad or 'none given'}; supported: {sorted(scenario.SUPPORTED)}")
    end = req.as_of or date.today()
    mkt = [f for f in FACTOR_TICKER if f in req.shocks]
    tick = [h.ticker for h in req.portfolio.holdings]
    ftick = [FACTOR_TICKER[f] for f in mkt]
    allt = tick + [t for t in ftick if t not in tick]
    df, deg = await get_prices(allt, lookback_start(end, 260), end, req.as_of)
    held = [h for h in req.portfolio.holdings if h.ticker in df.columns]
    if not held:
        return _fail("scenario", run_id, "no price data for any holding", t0)
    d, reason, warns = _deg_info(allt, df, deg)
    fr = pd.DataFrame(index=df.index)
    for f in FACTOR_TICKER:
        t = FACTOR_TICKER[f]
        if f in req.shocks and t in df.columns:
            fr[f] = df[t].diff() if f == "us10y" else df[t].pct_change()      # yield CHANGE in pp for us10y
    sens = load_json("sector_sensitivity.json")
    res = await asyncio.to_thread(scenario.run, req.portfolio, df[[h.ticker for h in held]].ffill(), fr, req.shocks, sens)
    judged = res["judgment_factors"]
    warns += [f"{f} uses judgment sensitivity, not a measured beta" for f in judged]
    parts = [f"{FACTOR_LABEL[f]} {s:+g}{'bps' if f in BPS_FACTORS else '%'}" for f, s in req.shocks.items()]
    summary = f"{', '.join(parts)} → ₹{res['pnl_inr']:,.0f} ({res['pnl_pct']:+.1%})"
    ev = make_evidence(run_id, "scenario", res, "quant factor model (OLS + judgment sensitivities)", _last_date(df),
                       0.4 if judged else 0.6, d, reason, _ms(t0), summary)
    return ToolResult(evidence=[ev], warnings=warns)


# ================================================================ /hedge_proposals
async def _hedge(req: HedgeReq, run_id: str | None) -> ToolResult:
    t0 = time.perf_counter()
    run_id = run_id or req.run_id
    _need_holdings(req.portfolio)
    end = req.as_of or date.today()
    tick = [h.ticker for h in req.portfolio.holdings]
    allt = tick + [c for c in req.candidates if c not in tick]
    df, deg = await get_prices(allt, lookback_start(end, 500), end, req.as_of)
    held = [h for h in req.portfolio.holdings if h.ticker in df.columns]
    if not held:
        return _fail("hedge", run_id, "no price data for any holding", t0)
    d, reason, warns = _deg_info(allt, df, deg)
    lots = {k: v for k, v in load_json("lot_sizes.json").items() if not k.startswith("_")}
    res = await asyncio.to_thread(hedges.propose, req.portfolio, df, req.candidates, req.target, lots, req.allow_options,
                                  req.horizon_days, req.reduce_var_pct, req.as_of or _last_date(df), req.evidence_ids)
    warns += res.pop("warnings", [])
    if not res["proposals"]:
        return _fail("hedge", run_id, "; ".join(warns) or "no hedge could be computed", t0)
    label = {"beta_neutral": "Beta-neutral", "min_variance": "Min-variance", "reduce_var_pct": "VaR-reduction"}[req.target]
    und = _short(res["proposals"][0]["underlying"])
    summary = f"{label} {und} hedge cuts {req.horizon_days}d VaR ₹{res['pre_var_inr']:,.0f} → ₹{res['post_var_inr']:,.0f}"
    ev = make_evidence(run_id, "hedge", res, "quant hedges_v1", _last_date(df), 0.7, d, reason, _ms(t0), summary)
    for p in res["proposals"]:                     # also cite this evidence itself
        p["evidence_ids"] = list(dict.fromkeys([*p["evidence_ids"], ev.id]))
    return ToolResult(evidence=[ev], warnings=warns)


# ================================================================ /exposure
async def _exposure(req: ExposureReq, run_id: str | None) -> ToolResult:
    t0 = time.perf_counter()
    run_id = run_id or req.run_id
    _need_holdings(req.portfolio)
    end = req.as_of or date.today()
    tick = [h.ticker for h in req.portfolio.holdings]
    allt = tick + ([req.benchmark] if req.benchmark not in tick else [])
    df, deg = await get_prices(allt, lookback_start(end, 260), end, req.as_of)
    held = [h for h in req.portfolio.holdings if h.ticker in df.columns]
    if not held:
        return _fail("exposure", run_id, "no price data for any holding", t0)
    d, reason, warns = _deg_info(allt, df, deg)
    bench = df[req.benchmark] if req.benchmark in df.columns else None
    sens = load_json("sector_sensitivity.json")
    res = await asyncio.to_thread(exposure.run, req.portfolio, df[[h.ticker for h in held]].ffill(), bench, sens)
    top = sorted(res["by_sector"].items(), key=lambda kv: -kv[1])[:2]
    summary = "Largest exposures: " + ", ".join(f"{s} {w:.0%}" for s, w in top)
    ev = make_evidence(run_id, "exposure", res, "quant exposure_v1 + sector_sensitivity.json (judgment)", _last_date(df),
                       0.8, d, reason, _ms(t0), summary)
    return ToolResult(evidence=[ev], warnings=warns)


# ================================================================ /bs_price (registry extra)
async def _bs(req: BsPriceReq, run_id: str | None) -> ToolResult:
    t0 = time.perf_counter()
    run_id = run_id or req.run_id
    o = options.bs(req.spot, req.strike, req.vol, req.rate, req.days / 365.0, req.kind)
    val = {k: round(v, 6) for k, v in o.items()} | {"kind": req.kind, "spot": req.spot, "strike": req.strike,
                                                   "vol": req.vol, "rate": req.rate, "days": req.days}
    ev = make_evidence(run_id, "hedge", val, "Black-Scholes (no dividends)", None, 0.7, False, None, _ms(t0),
                       f"BS {req.kind} {req.strike:g} ≈ ₹{o['price']:,.2f} (delta {o['delta']:.2f})")
    return ToolResult(evidence=[ev], warnings=["model price, not a market quote"])


# ================================================================ routes
@app.post("/event_study", response_model=ToolResult)
async def event_study_ep(req: EventStudyReq, x_run_id: str | None = Header(None)):
    return await mock_or("event_study", _event_study)(req, x_run_id)


@app.post("/correlations", response_model=ToolResult)
async def correlations_ep(req: CorrReq, x_run_id: str | None = Header(None)):
    return await mock_or("correlations", _correlations)(req, x_run_id)


@app.post("/var_montecarlo", response_model=ToolResult)
async def var_ep(req: RiskReq, x_run_id: str | None = Header(None)):
    return await mock_or("var_montecarlo", _risk)(req, x_run_id)


@app.post("/scenario", response_model=ToolResult)
async def scenario_ep(req: ScenarioReq, x_run_id: str | None = Header(None)):
    return await mock_or("scenario", _scenario)(req, x_run_id)


@app.post("/hedge_proposals", response_model=ToolResult)
async def hedge_ep(req: HedgeReq, x_run_id: str | None = Header(None)):
    return await mock_or("hedge_proposals", _hedge)(req, x_run_id)


@app.post("/exposure", response_model=ToolResult)
async def exposure_ep(req: ExposureReq, x_run_id: str | None = Header(None)):
    return await mock_or("exposure", _exposure)(req, x_run_id)


@app.post("/bs_price", response_model=ToolResult)
async def bs_ep(req: BsPriceReq, x_run_id: str | None = Header(None)):
    return await mock_or("bs_price", _bs)(req, x_run_id)
