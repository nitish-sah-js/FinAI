from __future__ import annotations
from datetime import datetime, date
from typing import Any, Literal
from pydantic import BaseModel, Field

# ---------- Evidence: every tool output is wrapped in this ----------
class Evidence(BaseModel):
    id: str                                  # ev_<tool>_<nnn>
    run_id: str | None = None
    tool: str                                # canonical tool name
    value: dict[str, Any]                    # tool-specific payload (schemas in §6)
    summary: str | None = None               # 1-line human summary (written by code, not LLM)
    source: str                              # "Open-Meteo forecast API", "yfinance", "gbm_v1 on Sentinel-2 NDVI"
    source_url: str | None = None
    as_of: datetime                          # when the underlying DATA is from
    timestamp: datetime                      # when this evidence was produced
    freshness_s: int | None = None           # timestamp - as_of, in seconds
    confidence: float | None = Field(None, ge=0, le=1)
    degraded: bool = False
    degraded_reason: str | None = None       # "api_timeout", "cache_fallback", "mock", "chaos"
    latency_ms: int | None = None
    model_version: str | None = None
    staleness_factor: float | None = None    # set by orchestrator (04): effective conf = confidence * staleness_factor

class ToolResult(BaseModel):
    """Standard response of EVERY tool endpoint on every service."""
    evidence: list[Evidence]
    warnings: list[str] = []

# ---------- Portfolio ----------
class Holding(BaseModel):
    ticker: str                    # RELIANCE.NS
    qty: float
    avg_price: float | None = None
    sector: str | None = None      # "Energy", "FMCG", "Agri", "Banks", ...

class Portfolio(BaseModel):
    portfolio_id: str = "demo"
    holdings: list[Holding]
    cash: float = 0.0
    currency: Literal["INR", "USD"] = "INR"

# ---------- Query ----------
class ChaosFlags(BaseModel):
    weather_down: bool = False
    force_rate_limit: bool = False       # cloud LLM returns 429 → must fall back to local
    agri_raster_missing: bool = False
    vector_down: bool = False
    slow_network_ms: int = 0

class QueryRequest(BaseModel):
    query: str
    portfolio: Portfolio | None = None   # None → load portfolio_id "demo"
    as_of: date | None = None            # time-machine mode: only use data <= as_of
    llm_mode: Literal["local", "boost", "auto"] | None = None   # None → env LLM_MODE
    lang: Literal["en", "hi", "hinglish"] = "en"
    chaos: ChaosFlags = ChaosFlags()
    ref_run_id: str | None = None        # for intent "explain": which earlier run to explain

class QueryAccepted(BaseModel):
    run_id: str
    ws_url: str                          # ws://L1:8000/ws/{run_id}

# ---------- Intent (output of P1) ----------
IntentType = Literal["event_impact", "portfolio_risk", "hedge_request", "explain", "market_summary"]
EventType = Literal["hurricane", "cyclone", "monsoon", "heatwave", "rates", "oil", "policy", "other"]

class Intent(BaseModel):
    intent: IntentType
    event_type: EventType | None = None
    region: str | None = None
    tickers: list[str] = []
    asset_classes: list[str] = []
    horizon_days: int = 5
    references_portfolio: bool = True
    needs_tools: list[str] = []          # subset of canonical tool names

# ---------- Agent outputs ----------
class AgentSignal(BaseModel):
    agent: str                                        # weather_agent
    signal: Literal["bullish", "bearish", "neutral", "mixed", "n/a"]
    summary: str                                      # ≤3 sentences, cites [ev_...]
    evidence_ids: list[str]
    confidence: float | None = None
    degraded: bool = False

class HedgeProposal(BaseModel):
    hedge_id: str
    instrument: str               # "NIFTY OCT FUT short", "RELIANCE 2900 PE"
    underlying: str               # ^NSEI
    side: Literal["buy", "sell"]
    quantity: float               # lots or shares
    unit: Literal["lots", "shares", "notional_inr"]
    hedge_ratio: float
    est_cost_inr: float | None = None
    rationale: str
    sizing_method: Literal["beta", "min_variance", "delta", "fixed"]
    evidence_ids: list[str]

class RedTeamReport(BaseModel):
    reasons: list[str]            # 3 strongest reasons it could be wrong, cite ev ids
    verdict: Literal["proceed", "proceed with caution", "do not act"]
    verdict_reason: str

class ValidatorReport(BaseModel):
    numbers_found: int
    numbers_matched: int
    unmatched: list[str]          # numbers stripped or flagged
    action: Literal["pass", "flagged", "stripped"]

class FinalAnswer(BaseModel):
    run_id: str
    query: str
    intent: Intent
    bottom_line: str
    holdings_impact: list[dict]   # {"ticker","impact","range","evidence_ids"}
    hedges: list[HedgeProposal]
    confidence: Literal["low", "medium", "high"]
    what_could_be_wrong: list[str]
    red_team: RedTeamReport | None
    validator: ValidatorReport
    signals: list[AgentSignal]
    evidence: list[Evidence]
    answer_markdown: str          # full rendered answer with [ev_...] citations
    lang: str = "en"
    llm_usage: dict = {}          # {"cloud_calls":2,"local_calls":7,"saved_calls":5,"providers":{...}}
    latency_ms: dict = {}         # per-stage: {"parse_intent":420,"fanout":2100,...,"total":6100}

# ---------- Live events (WebSocket) ----------
class AgentEvent(BaseModel):
    run_id: str
    seq: int                                # monotonically increasing per run
    node: str                               # node name (see §4)
    status: Literal["queued", "started", "progress", "finished", "failed", "skipped", "degraded"]
    ts: datetime
    latency_ms: int | None = None
    evidence_ids: list[str] = []
    message: str | None = None              # short human text for the UI
    model: str | None = None                # "qwen3:4b", "openai/gpt-oss-120b"
    provider: str | None = None             # "ollama@L1", "groq"
    host: str | None = None                 # "L2"
    tokens_in: int | None = None
    tokens_out: int | None = None
    meta: dict = {}

# ---------- Alerts ----------
class Alert(BaseModel):
    alert_id: str
    tier: Literal[1, 2, 3]                  # 1 pet nudge, 2 popup, 3 popup + Telegram/email
    kind: Literal["price_z", "volume_z", "news_burst", "sentiment_shift", "weather_threshold", "agri_stress"]
    tickers: list[str]
    headline: str                           # ≤ 25 words (P11)
    reason: str
    impact_score: float                     # 0..1, portfolio-weighted
    confidence: float
    evidence_ids: list[str] = []
    created_at: datetime
    cooldown_key: str                       # f"{kind}:{ticker}"
    deeplink: str                           # http://L3:3000/run/new?q=...
    acknowledged: bool = False

# ---------- Health ----------
class Health(BaseModel):
    service: str
    status: Literal["ok", "degraded", "down"]
    version: str = "0.1.0"
    mock: bool = False
    host: str | None = None
    uptime_s: int = 0
    deps: dict[str, str] = {}               # {"weaviate":"ok","ollama":"ok"}
    models: list[str] = []
