# 01 — Shared Contracts (SOURCE OF TRUTH)

> **Read this before building any module.** Every service, agent, UI and test uses the shapes,
> names, ports and conventions defined here. If you need a new field, add it **here first**
> (as an optional field with a default) and tell the team. Never invent a shape in your own module.

| | |
|---|---|
| **Owner** | Team lead (whoever owns L1 "Brain"); everyone reviews |
| **Build first** | Hour 0–1. Every other module depends on this |
| **Provides** | Python package `copilot_common` (Pydantic v2 models, IDs, cache, service base, fixtures) + TypeScript types for the frontend |

---

## 1. Laptop, port and model map (fixed)

| Laptop | Role | Service | Port | Folder |
|---|---|---|---|---|
| **L1 "Brain"** | Orchestration | Orchestrator API + WebSocket (LangGraph) | **8000** | `services/orchestrator` |
| L1 | | Ollama: `qwen3:4b-instruct` (intent, synthesizer, explain) | 11434 | — |
| L1 | | SQLite ledger + LangGraph checkpoints | file | `data/ledger.db` |
| **L2 "Quant & ML"** | Compute | Quant engine | **8101** | `services/quant` |
| L2 | | Sentiment (FinBERT) | **8102** | `services/sentiment` |
| L2 | | Agri / ISRO model | **8103** | `services/agri` |
| L2 | | Vector / analogs service (embeddings + Weaviate client) | **8104** | `services/vectordb` |
| L2 | | Weaviate (Docker) | 8080 (HTTP), 50051 (gRPC) | `infra/docker-compose.yml` |
| L2 | | Ollama: `gemma3:4b` (narrators, sentiment 2nd opinion) | 11434 | — |
| **L3 "Edge & UI"** | Data + UI | Ingestion service | **8201** | `services/ingestion` |
| L3 | | Monitor / alerts service | **8202** | `services/monitor` |
| L3 | | Next.js terminal | **3000** | `apps/terminal` |
| L3 | | Electron desktop pet | — | `apps/pet` |
| L3 | | Ollama: `qwen3:1.7b` (alerts), `phi4-mini` (red team) | 11434 | — |

**Demo-day fallback:** set every host to `127.0.0.1` and run everything on L1. Every module must work that way too.

### Cloud "boost" LLMs (optional, free tier, all OpenAI-compatible)
| Priority | Provider | Model | base_url | Env key |
|---|---|---|---|---|
| 1 | Groq | `openai/gpt-oss-120b` | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` |
| 2 | Groq | `qwen/qwen3-32b` | same | `GROQ_API_KEY` |
| 3 | Cerebras | `gpt-oss-120b` | `https://api.cerebras.ai/v1` | `CEREBRAS_API_KEY` |
| 4 | OpenRouter | `moonshotai/kimi-k2.6:free` | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| 5 (always) | Local Ollama | `qwen3:4b-instruct` | `http://${L1_HOST}:11434/v1` | — |

Model IDs and free-tier limits change. Verify them on the provider dashboards on day 0. All routing logic lives in `packages/copilot_llm` (see [03_LLM_GATEWAY.md](03_LLM_GATEWAY.md)).

---

## 2. Repository layout (one monorepo, cloned on all 3 laptops)

```
copilot/
├── packages/
│   ├── copilot_common/          # THIS CONTRACT (pip install -e packages/copilot_common)
│   │   ├── copilot_common/
│   │   │   ├── models.py        # all Pydantic models below
│   │   │   ├── ids.py           # new_run_id(), new_evidence_id()
│   │   │   ├── settings.py      # reads .env (hosts, ports, MOCK, CACHE_MODE)
│   │   │   ├── cache.py         # file cache: record / replay / off
│   │   │   ├── service_base.py  # create_service_app(): /health, mock, CORS, timing
│   │   │   └── fixtures/        # canned JSON per endpoint (used by MOCK=1 and frontend)
│   │   └── pyproject.toml
│   └── copilot_llm/             # LLM gateway library (03)
├── services/
│   ├── orchestrator/  (L1)      # 04
│   ├── quant/         (L2)      # 06
│   ├── sentiment/     (L2)      # 07
│   ├── vectordb/      (L2)      # 08
│   ├── agri/          (L2)      # 09
│   ├── ingestion/     (L3)      # 10
│   └── monitor/       (L3)      # 11
├── backtest/                    # 12
├── apps/
│   ├── terminal/      (L3)      # 13 Next.js
│   └── pet/           (L3)      # 14 Electron
├── data/
│   ├── cache/<service>/<sha256>.json
│   ├── raw/ (rasters, csv)      # gitignored
│   ├── events.json              # historical events (08)
│   ├── region_exposure.json     # region→equity map (09)
│   └── portfolio_demo.csv
├── infra/
│   ├── docker-compose.yml       # Weaviate
│   ├── check_health.py
│   └── env/ L1.env L2.env L3.env
└── docs/                        # these files
```

Python 3.11+, Pydantic v2, FastAPI, httpx. Node 20+ for the apps. The code is shared through a GitHub repo, and each laptop runs `git pull`.

---

## 3. Environment variables (`.env`, one per laptop, same keys everywhere)

```dotenv
# hosts (use 127.0.0.1 for single-laptop mode)
L1_HOST=192.168.43.101
L2_HOST=192.168.43.102
L3_HOST=192.168.43.103

ORCH_URL=http://${L1_HOST}:8000
QUANT_URL=http://${L2_HOST}:8101
SENTIMENT_URL=http://${L2_HOST}:8102
AGRI_URL=http://${L2_HOST}:8103
VECTOR_URL=http://${L2_HOST}:8104
WEAVIATE_HOST=${L2_HOST}
INGEST_URL=http://${L3_HOST}:8201
MONITOR_URL=http://${L3_HOST}:8202

OLLAMA_L1=http://${L1_HOST}:11434
OLLAMA_L2=http://${L2_HOST}:11434
OLLAMA_L3=http://${L3_HOST}:11434

MOCK=0                 # 1 = every service returns fixtures, no models/APIs needed
CACHE_MODE=record      # record | replay | off
LLM_MODE=local         # local | boost | auto
GROQ_API_KEY=
CEREBRAS_API_KEY=
OPENROUTER_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
FRED_API_KEY=          # free key (10)
EIA_API_KEY=           # free key (10)

AUTO_MIN_RPD=50        # LLM_MODE=auto: use cloud only while remaining daily requests > this (03)
LLM_REPLAY_STRICT=0    # 1 = CACHE_MODE=replay raises on an LLM cache miss instead of calling the model (03)
MOCK_DELAY_MS=300      # artificial latency per mocked call, so the live graph animates realistically
EMBED_DEVICE=cpu       # cpu | cuda, for bge-small embeddings (08)
```

`settings.py` expands `${VAR}` and exposes a `Settings` object (pydantic-settings).

---

## 4. Naming conventions

| Thing | Format | Example |
|---|---|---|
| Run ID | `run_<yyyymmddHHMMSS>_<4 hex>` | `run_20261003141502_a91f` |
| Evidence ID | `ev_<tool>_<3-digit seq within run>` | `ev_weather_001` |
| Alert ID | `al_<yyyymmdd>_<6 hex>` | `al_20261003_3fa21c` |
| Tool names (canonical) | `sentiment, weather, agri, macro, analogs, exposure, risk, hedge, event_study, scenario, correlations, news, prices, hedge_validation` | |
| Agent/node names | `parse_intent, router, sentiment_agent, weather_agent, agri_agent, macro_agent, analog_agent, exposure_agent, join, quant_agent, synthesizer, red_team, validator` | |
| Tickers | Yahoo format. NSE uses the `.NS` suffix, indices use `^` | `RELIANCE.NS`, `^NSEI`, `CL=F`, `NG=F`, `INR=X` |
| Dates | ISO 8601. Timestamps are UTC with `Z` | `2026-10-03T08:45:00Z` |
| Returns | Decimal fractions, not percent | `0.052` = 5.2% |
| Percent fields | Field name ends in `_pct` | `rain_anomaly_pct: -22` |

Every HTTP call between services carries the header **`X-Run-Id`**. Services include it in their logs and use it to build evidence IDs.

---

## 5. Core models (`copilot_common/models.py`)

Copy these verbatim. They are the contract.

```python
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
    exclude_holdout: bool = False        # backtest (12 §A3.2): analog search skips held-out events

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
    auto_cited: list[str] = []    # uncited figures that matched exactly ONE evidence item; the citation was inserted

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
    model: str | None = None                # "qwen3:4b-instruct", "openai/gpt-oss-120b"
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
```

---

## 6. Tool payloads (`Evidence.value` per tool)

Each module file has the full spec and example. This table is the index, and field names must match.

| tool | Endpoint (service) | `value` keys (required) |
|---|---|---|
| `prices` | `POST /prices` (ingestion) | `ticker, interval, rows:[{date,open,high,low,close,volume}]` |
| `news` | `POST /news` (ingestion) | `items:[{news_id,title,summary,source,url,published_at,tickers,category}]`. `category` is `"news"` or `"corporate_announcement"`, and `/announcements` returns the same shape |
| `weather` | `POST /weather/features` (ingestion) | `region, lat, lon, rain_anomaly_pct, heat_index_c, max_temp_c, soil_moisture_0_7cm, storm:{name,category,basin,track_toward}|null, alerts:[str], horizon_days`. Optional: `soil_moisture_z, daily:[...], lookahead_risk` |
| `macro` | `POST /macro/features` (ingestion) | `repo_rate, repo_change_bps, cpi_yoy, usd_inr, usd_inr_chg_5d, brent, brent_chg_5d, us10y, us10y_chg_5d`. Optional: `series_dates` |
| `sentiment` | `POST /sentiment/score` (sentiment) | `items:[{news_id,label,score,confidence,model,relevance,hinglish,second_opinion|null,weight}], portfolio_sentiment, by_ticker:{T:score}`. Labels are lowercase `positive|neutral|negative`. `second_opinion` = P3 JSON + `model` |
| `agri` | `POST /agri_signal` (agri) | Full schema in 09 §Output: `region_id, district, state, crop_season, season_year, main_crops, lat, lon, period_start, period_end, features{ndvi_mean, ndvi_anomaly_z, vci, ndvi_delta, rain_anomaly_pct, rain_30d_mm, soil_moisture_0_7cm, soil_moisture_anomaly_z, …}, stress_class (16-day forecast), stress_class_now (rule label), class_probs, yield_anomaly_pct{q10,q50,q90}\|null (null: no yield model), stress_lead_days, trend{ndvi_anomaly_z_prev, direction, …}, linked_equities[{ticker, link, sign, strength}], baseline_years ("2011-2025"), n_years_baseline, imagery_age_days, model_skill, confidence, model_version, data_source, label_source, degraded`. Only the 8 modelled districts (services/agri `GET /regions`); others → degraded `unknown_region` |
| `analogs` | `POST /find_analogs` (vectordb) | `analogs:[...], distribution:[{asset,horizon,measure,n,median,p10,p90,conformal_lo,conformal_hi,coverage_target,n_calib}]` (**a list, one entry per asset**), `confidence, filters_relaxed`. The request accepts `as_of` and `exclude_holdout: bool` |
| `exposure` | `POST /exposure` (quant) | `by_sector:{S:weight}, by_ticker:[{ticker,weight,beta,weather_sens,agri_sens}], heatmap:{rows:[sector],cols:["weather","agri","crude","rates","usd_inr"],values:[[float]],row_weights?:{S:w}}` |
| `event_study` | `POST /event_study` (quant) | `ticker, event_date, window, car, car_t, p_value, ar_series` |
| `correlations` | `POST /correlations` (quant) | `pairs:[{a,b,lag,corr}]` |
| `risk` | `POST /var_montecarlo` (quant) | `method, horizon_days, confidence_level, var_inr, cvar_inr, var_pct, paths, pnl_hist:{bin_edges,counts}` |
| `scenario` | `POST /scenario` (quant), proxied by `POST /tools/scenario` (orchestrator) | Request `shocks` keys: `crude, usd_inr, nifty, monsoon_rain` in **percent points**, plus `repo_bps` in basis points. Value: `shocks, pnl_inr, pnl_pct, by_ticker` |
| `hedge_validation` | `POST /hedge_validation` (quant) | `n, n_improved, median_dd_reduction_pp, range_dd_reduction_pp, range_kind, events[], skipped[], optimizer{adopt, decision}`. Out-of-sample: hedge fitted only on data before each event |
| `hedge` | `POST /hedge_proposals` (quant) | `proposals:[HedgeProposal], pre_var_inr, post_var_inr, portfolio_beta`. `hedge_id` is stable within a run (paper trading relies on it) |

Region IDs use the `<STATE>-<District>` format (`OD-Puri`, `MH-Yavatmal`). Non-Indian regions use `<COUNTRY>-<Region>` (`US-GulfCoast`).

---

## 7. Standard service behaviour (`service_base.create_service_app`)

Every Python service **must** be built with this helper so they all behave the same:

```python
def create_service_app(name: str, version: str = "0.1.0", deps_check=None) -> FastAPI:
    """Adds: GET /health (Health model), CORS *, request timing header X-Latency-Ms,
    X-Run-Id propagation, MOCK mode middleware, and exception→ToolResult(degraded) handler."""
```

1. **`GET /health`** returns `Health`. The dashboard polls it every 5 s.
2. **`MOCK=1`.** Every tool endpoint returns `fixtures/<service>/<endpoint>.json` with `degraded=true, degraded_reason="mock"`, without calling any model or API, after waiting `MOCK_DELAY_MS`. The LLM gateway returns `fixtures/llm/<role>.json` (canned outputs per role: `intent`, `narrator_weather`, `synthesizer`, `red_team`, `alert`, ...).
3. **Cache** (`cache.py`):
   ```python
   async def cached(service: str, key_obj: dict, fn, ttl_s: int | None = None):
       """key = sha256(json.dumps(key_obj, sort_keys=True)). CACHE_MODE=record: call fn, save.
       replay: return saved (raise CacheMiss if absent). off: always call fn."""
   ```
4. **Never raise to the caller.** On internal failure, return a `ToolResult` whose evidence has `degraded=true`, the last cached value (if any), `confidence` × 0.5 and a `degraded_reason`. HTTP status is still 200. Use a 4xx only for invalid input.
5. **Chaos.** If the request body contains `"chaos": {...}` or the header `X-Chaos: weather_down,...` is set, simulate that failure.
6. **Time machine.** If the request has `as_of`, use only data with timestamp ≤ `as_of`.
7. **Staleness penalty.** `freshness_s` is set by the producer. The orchestrator multiplies confidence by `staleness_factor(tool, freshness_s)`, defined in 04.

---

## 8. WebSocket protocol (orchestrator, L1:8000)

- `POST /query` with a `QueryRequest` returns `QueryAccepted`.
- `WS /ws/{run_id}` streams `{"type":"event","data":AgentEvent}` messages, then one `{"type":"final","data":FinalAnswer}`.
- `WS /ws/alerts` (served by the monitor on L3:8202) streams `{"type":"alert","data":Alert}`.
- `GET /runs/{run_id}` returns the stored `FinalAnswer` plus every event, for replay and "explain yourself".
- `GET /runs?limit=20` lists recent runs.
- `WS /ws/activity` streams every `AgentEvent` from every run. The pet uses it, and falls back to polling `GET /runs?limit=1`.
- The `parse_intent` **finished** event carries the parsed `Intent` in `meta.intent`, so the UI can show the decomposition early.
- `FinalAnswer.llm_usage` has the shape `{"cloud_calls":int,"local_calls":int,"saved_calls":int,"providers":{"<provider>":{"used":int,"limit":int|null,"model":str}}}`.

### Endpoint registry (every HTTP route in the system)
| Service | Routes | Spec |
|---|---|---|
| Orchestrator L1:8000 | `POST /query` · `WS /ws/{run_id}` · `WS /ws/activity` · `GET /runs` · `GET /runs/{id}` · `GET /runs/{id}/events.jsonl` · `GET /runs/{id}/explain` · `POST /runs/{id}/resume` · `GET /llm/quota` · `GET /health/all` · `GET/POST /portfolio` · `GET /portfolio/{id}` · `POST /portfolio/upload` · `POST /tools/scenario` (proxy to quant) · `GET /backtest/scoreboard` · `/paper/propose` · `/paper/approve` · `/paper/positions` · `/paper/mark` · `/paper/close` · `/paper/history` | 04, 12 |
| Quant L2:8101 | `/event_study` · `/correlations` · `/var_montecarlo` · `/scenario` · `/scenario/from_evidence` · `/hedge_proposals` · `/hedge_validation` · `/exposure` · `/bs_price` | 06 |
| Sentiment L2:8102 | `/sentiment/score` · `/sentiment/eval` | 07 |
| Agri L2:8103 | `/agri_signal` · `/agri_signal/batch` · `GET /regions` · `GET /model_info` | 09 |
| Vector L2:8104 | `/find_analogs` · `/news/index` · `/news/search` · `/embed` · `GET /latency` | 08 |
| Ingestion L3:8201 | `/prices` · `/news` · `/weather/features` · `/macro/features` · `/announcements` · `GET /feed/since` · `GET /regions` | 10 |
| Monitor L3:8202 | `WS /ws/alerts` · `GET /alerts` · `POST /alerts/{id}/ack` · `POST /alerts/test` · `GET /monitor/state` | 11 |

Every service also exposes `GET /health`.

### Shared data files (owner in brackets)
`data/events.json` (backtest holdout events, 12), `data/historical_events.json` (analog corpus), `data/events_seed.csv` (each record has `split: "train" | "holdout"`), `data/conformal_q.json` [08] · `data/region_exposure.json` with shape `{"regions": {region_id: {"crops": [...], "equities": [{ticker, link, sign, strength}]}}, "sector_defaults": {...}}` (09 §9; readers also accept the flat `{region_id: [{ticker, strength}]}`), `data/regions.json`, `data/agri/*` [09] · `data/lot_sizes.json` (check against NSE), `data/sector_sensitivity.json`, `data/ticker_aliases.json` [06] · `data/portfolio_demo.csv` [04] · `data/backtest/scoreboard.json` [12]

Example event stream for one run:
```json
{"type":"event","data":{"run_id":"run_20261003141502_a91f","seq":1,"node":"parse_intent","status":"started","ts":"2026-10-03T08:45:02Z","model":"qwen3:4b-instruct","provider":"ollama@L1","host":"L1"}}
{"type":"event","data":{"run_id":"run_20261003141502_a91f","seq":2,"node":"parse_intent","status":"finished","ts":"2026-10-03T08:45:03Z","latency_ms":610,"message":"event_impact · cyclone · Odisha · 5d"}}
{"type":"event","data":{"run_id":"run_20261003141502_a91f","seq":3,"node":"weather_agent","status":"started","ts":"2026-10-03T08:45:03Z","host":"L3"}}
{"type":"event","data":{"run_id":"run_20261003141502_a91f","seq":9,"node":"weather_agent","status":"degraded","ts":"2026-10-03T08:45:05Z","latency_ms":1800,"evidence_ids":["ev_weather_001"],"message":"Open-Meteo timeout → cached value from 6h ago"}}
{"type":"final","data":{ "...FinalAnswer..." : "..." }}
```

---

## 9. TypeScript mirror (`apps/terminal/lib/contracts.ts`)

Generate it from the Pydantic models so the two never drift:
`python -m copilot_common.export_schema > apps/terminal/lib/schema.json`, then `npx json-schema-to-typescript schema.json > contracts.ts`.
The frontend must not hand-write these types.

---

## 10. Build prompt for this module (paste into any coding LLM)

```
You are building the shared contract package for a multi-service Python project.
Create packages/copilot_common with pyproject.toml (name copilot_common, deps: pydantic>=2.6,
pydantic-settings, fastapi, httpx, python-dotenv).

Step 1. Create copilot_common/models.py containing EXACTLY the models in the spec I paste below
        (Evidence, ToolResult, Holding, Portfolio, ChaosFlags, QueryRequest, QueryAccepted, Intent,
        AgentSignal, HedgeProposal, RedTeamReport, ValidatorReport, FinalAnswer, AgentEvent, Alert, Health).
Step 2. ids.py: new_run_id() -> "run_<yyyymmddHHMMSS>_<4hex>"; EvidenceCounter(run_id).next(tool) -> "ev_<tool>_<nnn>"
        (thread-safe, per run); new_alert_id().
Step 3. settings.py: pydantic-settings Settings reading .env with ${VAR} expansion; fields for every key in the spec.
Step 4. cache.py: async cached(service, key_obj, fn, ttl_s=None) with CACHE_MODE record|replay|off,
        files at data/cache/<service>/<sha256>.json storing {"key":..., "saved_at":..., "value":...}. CacheMiss exception.
Step 5. service_base.py: create_service_app(name, version, deps_check) -> FastAPI with GET /health (Health),
        CORS allow all, middleware adding X-Latency-Ms and propagating X-Run-Id (contextvar get_run_id()),
        a helper mock_or(endpoint_name, real_fn) that returns fixtures when MOCK=1, and a degraded()
        helper building an Evidence with degraded=True.
Step 6. export_schema.py: prints a JSON Schema bundle of all models (for TypeScript generation).
Step 7. fixtures/: one JSON file per endpoint listed in the spec's tool table, each a valid ToolResult.
Step 8. tests/: round-trip every fixture through ToolResult.model_validate; test ids format; test cache record→replay.
Output complete files. No placeholders.
[PASTE SECTIONS 4-7 OF 01_CONTRACTS.md HERE]
```

### Acceptance checklist
- [ ] `pip install -e packages/copilot_common` works on all 3 laptops
- [ ] `pytest packages/copilot_common` passes, and every fixture validates
- [ ] `python -m copilot_common.export_schema` prints JSON Schema
- [ ] A sample service built with `create_service_app("demo")` answers `/health` with `mock` reflecting `MOCK`

### Change process
Add fields only as **optional with defaults**. Bump `version` in `pyproject.toml`, post in the team chat, and everyone runs `git pull`.
