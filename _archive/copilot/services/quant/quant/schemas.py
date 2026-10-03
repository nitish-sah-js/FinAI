from __future__ import annotations
from datetime import date
from typing import Literal
from pydantic import BaseModel
from copilot_common.models import Portfolio


class EventStudyReq(BaseModel):
    ticker: str; event_date: date
    benchmark: str = "^NSEI"
    est_window: tuple[int, int] = (-120, -11)    # trading days relative to event
    event_window: tuple[int, int] = (-1, 5)
    as_of: date | None = None; run_id: str | None = None


class CorrReq(BaseModel):
    tickers: list[str]                   # e.g. ["RELIANCE.NS","CL=F","INR=X"]
    lags: list[int] = [0, 1, 2, 5]       # b lagged by k days vs a
    lookback_days: int = 250
    method: Literal["pearson", "spearman"] = "pearson"
    as_of: date | None = None; run_id: str | None = None


class RiskReq(BaseModel):
    portfolio: Portfolio
    method: Literal["historical", "parametric", "montecarlo"] = "montecarlo"
    horizon_days: int = 5
    confidence_level: float = 0.95
    n_paths: int = 10_000
    lookback_days: int = 500
    seed: int = 42
    as_of: date | None = None; run_id: str | None = None


class ScenarioReq(BaseModel):
    portfolio: Portfolio
    shocks: dict[str, float]     # factor -> pct move (repo_bps, us10y: basis points)
    as_of: date | None = None; run_id: str | None = None


class HedgeReq(BaseModel):
    portfolio: Portfolio
    target: Literal["beta_neutral", "min_variance", "reduce_var_pct"] = "min_variance"
    reduce_var_pct: float = 30
    candidates: list[str] = ["^NSEI", "^NSEBANK"]   # hedge underlyings
    allow_options: bool = True
    horizon_days: int = 5
    evidence_ids: list[str] = []     # ADDED (optional): ids of risk/exposure evidence to cite in each proposal
    as_of: date | None = None; run_id: str | None = None


class ExposureReq(BaseModel):
    portfolio: Portfolio
    benchmark: str = "^NSEI"
    as_of: date | None = None; run_id: str | None = None


class BsPriceReq(BaseModel):
    """ADDED: /bs_price is in the 01 endpoint registry but not specified in 06. Black-Scholes helper."""
    spot: float; strike: float
    vol: float                       # annualised, decimal (0.18 = 18%)
    days: float                      # calendar days to expiry
    rate: float = 0.065
    kind: Literal["call", "put"] = "put"
    run_id: str | None = None
