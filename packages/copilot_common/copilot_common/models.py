"""All shared Pydantic models. Source of truth: docs/01_CONTRACTS.md §5."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------- Evidence: every tool output is wrapped in this ----------
class Evidence(BaseModel):
    id: str                                  # ev_<tool>_<nnn>
    run_id: str | None = None
    tool: str                                # canonical tool name
    value: dict[str, Any]                    # tool-specific payload (schemas in 01 §6)
    summary: str | None = None               # 1-line human summary (written by code, not LLM)
    source: str
    source_url: str | None = None
    as_of: datetime                          # when the underlying DATA is from
    timestamp: datetime                      # when this evidence was produced
    freshness_s: int | None = None           # timestamp - as_of, in seconds
    confidence: float | None = Field(None, ge=0, le=1)
    degraded: bool = False
    degraded_reason: str | None = None       # "api_timeout", "cache_fallback", "mock", "chaos"
    latency_ms: int | None = None
    model_version: str | None = None
    staleness_factor: float | None = None    # set by orchestrator (04)
    fixture: bool = False                    # canned test data; only legitimate when MOCK=1 (validator rejects it otherwise)
    synthetic: bool = False                  # demo data that is not real (e.g. the DEMO-ODISHA storm); shown as SIMULATED


class ToolResult(BaseModel):
    """Standard response of EVERY tool endpoint on every service."""
    evidence: list[Evidence]
    warnings: list[str] = []


# ---------- Portfolio ----------
class Holding(BaseModel):
    ticker: str
    qty: float
    avg_price: float | None = None
    sector: str | None = None


class Portfolio(BaseModel):
    portfolio_id: str = "demo"
    holdings: list[Holding]
    cash: float = 0.0
    currency: Literal["INR", "USD"] = "INR"


# ---------- News -> sentiment (ingestion worker and orchestrator call sentiment /sentiment/score) ----------
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NewsScoreItem(BaseModel):
    """One headline to score. published_at defaults to now when the feed has no date."""
    news_id: str
    title: str
    summary: str = ""
    source: str = ""
    published_at: datetime = Field(default_factory=_utcnow)
    tickers: list[str] = []


class SentimentScoreRequest(BaseModel):
    items: list[NewsScoreItem]
    portfolio: Portfolio | None = None
    second_opinion: bool = True
    as_of: date | None = None
    run_id: str | None = None


# ---------- Query ----------
class ChaosFlags(BaseModel):
    weather_down: bool = False
    force_rate_limit: bool = False
    agri_raster_missing: bool = False
    vector_down: bool = False
    slow_network_ms: int = 0


class QueryRequest(BaseModel):
    query: str
    portfolio: Portfolio | None = None
    as_of: date | None = None
    llm_mode: Literal["local", "boost", "auto"] | None = None
    lang: Literal["en", "hi", "hinglish"] = "en"
    chaos: ChaosFlags = ChaosFlags()
    ref_run_id: str | None = None
    exclude_holdout: bool = False        # backtest (12 §A3.2): analog search must skip held-out events
    alert_id: str | None = None          # set when a run starts from a monitor alert deep link (11 §13)
    no_cache: bool = False               # skip LLM cache reads for this run (scripts/verify_usage.py wants real calls)


class QueryAccepted(BaseModel):
    run_id: str
    ws_url: str


# ---------- Intent (output of P1) ----------
IntentType = Literal["event_impact", "portfolio_risk", "hedge_request", "explain", "market_summary",
                     "stock_lookup", "rank_exposure", "what_if",                                  # analysis (agents run)
                     "greeting", "smalltalk", "thanks", "help", "out_of_scope", "unclear"]        # conversation (no agents)
CONVERSATIONAL_INTENTS = ("greeting", "smalltalk", "thanks", "help", "out_of_scope", "unclear")
EventType = Literal["hurricane", "cyclone", "monsoon", "heatwave", "rates", "oil", "policy", "other"]


class Intent(BaseModel):
    intent: IntentType
    event_type: EventType | None = None
    region: str | None = None
    tickers: list[str] = []
    asset_classes: list[str] = []
    horizon_days: int = 5
    references_portfolio: bool = True
    needs_tools: list[str] = []


# ---------- Agent outputs ----------
class AgentSignal(BaseModel):
    agent: str
    signal: Literal["bullish", "bearish", "neutral", "mixed", "n/a"]
    summary: str
    evidence_ids: list[str]
    confidence: float | None = None
    degraded: bool = False


class HedgeProposal(BaseModel):
    hedge_id: str
    instrument: str
    underlying: str
    side: Literal["buy", "sell"]
    quantity: float
    unit: Literal["lots", "shares", "notional_inr"]
    hedge_ratio: float
    est_cost_inr: float | None = None
    rationale: str
    sizing_method: Literal["beta", "min_variance", "delta", "fixed"]
    evidence_ids: list[str]


class RedTeamReport(BaseModel):
    reasons: list[str]
    verdict: Literal["proceed", "proceed with caution", "do not act"]
    verdict_reason: str


class ValidatorReport(BaseModel):
    numbers_found: int
    numbers_matched: int
    unmatched: list[str]
    action: Literal["pass", "flagged", "stripped"]
    auto_cited: list[str] = []      # "₹48,200→ev_risk_001": uncited figure matched exactly one evidence item
    rejected_evidence: list[str] = []   # evidence ids not allowed as a source (fixture data outside MOCK mode)


class FinalAnswer(BaseModel):
    run_id: str
    query: str
    intent: Intent
    bottom_line: str
    holdings_impact: list[dict]
    hedges: list[HedgeProposal]
    confidence: Literal["low", "medium", "high"]
    what_could_be_wrong: list[str]
    red_team: RedTeamReport | None
    validator: ValidatorReport
    signals: list[AgentSignal]
    evidence: list[Evidence]
    answer_markdown: str
    lang: str = "en"
    llm_usage: dict = {}
    latency_ms: dict = {}
    kind: Literal["analysis", "conversation"] = "analysis"   # conversation = greeting / help / unclear ...: no agents, no numbers
    suggestions: list[str] = []                               # clickable follow-up questions


# ---------- Live events (WebSocket) ----------
class AgentEvent(BaseModel):
    run_id: str
    seq: int
    node: str
    status: Literal["queued", "started", "progress", "finished", "failed", "skipped", "degraded"]
    ts: datetime
    latency_ms: int | None = None
    t_ms: int | None = None              # ms since the run started (for a start/end waterfall)
    evidence_ids: list[str] = []
    message: str | None = None
    model: str | None = None
    provider: str | None = None
    host: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    meta: dict = {}


# ---------- Alerts ----------
class Alert(BaseModel):
    alert_id: str
    tier: Literal[1, 2, 3]
    kind: Literal["price_z", "volume_z", "news_burst", "sentiment_shift", "weather_threshold", "agri_stress"]
    tickers: list[str]
    headline: str
    reason: str
    impact_score: float
    confidence: float
    evidence_ids: list[str] = []
    created_at: datetime
    cooldown_key: str
    deeplink: str
    acknowledged: bool = False


# ---------- Health ----------
class Health(BaseModel):
    service: str
    status: Literal["ok", "degraded", "down"]
    version: str = "0.1.0"
    mock: bool = False
    host: str | None = None
    uptime_s: int = 0
    deps: dict[str, str] = {}
    models: list[str] = []
    gpu: dict = {}                       # {name, mem_used_mb, mem_total_mb} from nvidia-smi, {} if no GPU
