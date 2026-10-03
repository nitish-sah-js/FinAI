# 04 — Orchestrator (LangGraph state machine + FastAPI/WebSocket)

| | |
|---|---|
| **Owner / Laptop** | L1 "Brain" owner, `services/orchestrator`, port **8000** |
| **Depends on** | 01 (all models), 03 (`copilot_llm`), 05 (prompts). It calls 06–10 over HTTP, and all of them have mock fixtures, so you can build this before they exist |
| **Provides** | `POST /query`, `WS /ws/{run_id}`, `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/explain`, `POST /runs/{id}/resume`, `GET /llm/quota`, `GET /health/all`, `GET/POST /portfolio` |
| **Build when** | H2–H14 (stub tools by H8, real tools by H14) |

---

## 1. Folder layout

```
services/orchestrator/
├── app.py                 # FastAPI: endpoints + WebSocket hub
├── graph.py               # build_graph() -> compiled StateGraph
├── state.py               # RunState TypedDict + reducers
├── router.py              # deterministic intent → agents table
├── nodes/
│   ├── parse_intent.py    # P1 via llm.chat("intent")
│   ├── agents.py          # 6 fan-out agents (HTTP tool call + narrator)
│   ├── join.py
│   ├── quant_agent.py     # risk, hedge, event_study, scenario
│   ├── planner.py         # optional P2 extra tools (max 2)
│   ├── synthesizer.py     # P8
│   ├── red_team.py        # P9
│   ├── validator.py       # Numbers Ledger (code, no LLM)
│   └── explain.py         # P10 replay
├── tools_client.py        # typed httpx calls to L2/L3 services, returns ToolResult
├── staleness.py           # staleness_factor()
├── budget.py              # AGENT_BUDGET + enforcement
├── events.py              # EventBus: emit(AgentEvent) → WS subscribers + SQLite
├── ledger.py              # SQLite: runs, events, evidence, answers
├── portfolio.py           # load demo CSV / upload / save
├── prompts/               # P1..P11 text files (from 05)
└── tests/
```

## 2. Graph

```
START → parse_intent → router ──Send──┬─ sentiment_agent ─┐
                                      ├─ weather_agent ───┤
                                      ├─ agri_agent ──────┤
                                      ├─ macro_agent ─────┤
                                      ├─ analog_agent ────┤
                                      └─ exposure_agent ──┘
                                                          ▼
                                   join → [planner (optional, ≤2 tools)] → quant_agent
                                        → synthesizer → red_team → validator → END
intent == "explain"  →  explain → END
```

### State (`state.py`)
```python
class RunState(TypedDict, total=False):
    run_id: str
    request: dict                                     # QueryRequest
    portfolio: dict                                   # Portfolio
    intent: dict                                      # Intent
    selected_agents: list[str]
    evidence: Annotated[list[dict], operator.add]     # reducer: fan-out branches append
    signals: Annotated[list[dict], operator.add]      # AgentSignal per agent
    quant: dict                                       # {"risk":ev_id,"hedge":ev_id,...}
    draft: str                                        # synthesizer markdown
    red_team: dict
    validator: dict
    final: dict                                       # FinalAnswer
    steps: int
    errors: Annotated[list[str], operator.add]
```

### Router (deterministic, `router.py`)
| intent | always | conditional |
|---|---|---|
| `event_impact` | weather, analogs, exposure, sentiment | `agri` if event_type ∈ {monsoon, heatwave, cyclone} or the region is an agri district · `macro` if event_type ∈ {oil, rates, policy} |
| `portfolio_risk` | exposure, sentiment, macro | `weather` if any holding has `weather_sens` > 0.3 |
| `hedge_request` | exposure, macro, analogs | — |
| `market_summary` | sentiment, macro | `weather` if an active storm or heat alert exists |
| `explain` | (no fan-out, go to the explain node) | — |

`Intent.needs_tools` can **add** agents but never removes the "always" set. Quant runs after the join for everything except explain: `risk` always, `hedge` if the intent is hedge_request or event_impact, `scenario` if the query contains shock words such as "if crude +10%".

### Fan-out with `Send`
```python
def route_agents(state: RunState) -> list[Send]:
    return [Send(agent, {"run_id": state["run_id"], "intent": state["intent"],
                         "portfolio": state["portfolio"], "request": state["request"]})
            for agent in state["selected_agents"]]
builder.add_conditional_edges("router", route_agents, AGENT_NODES)
for a in AGENT_NODES: builder.add_edge(a, "join")
```
**Caveat:** `Send` branches run in parallel only when the node functions are **async and awaiting I/O**. Every agent node must be `async def` and use `httpx.AsyncClient`. Wrap anything blocking or CPU-bound that runs in-process (pandas, numpy, local fallback computations) with `await asyncio.to_thread(fn, ...)`. Heavy compute (FinBERT, rasterio, Monte Carlo) lives on L2 behind HTTP for exactly this reason.

### Agent node pattern (`nodes/agents.py`)
```python
async def weather_agent(s) -> dict:
    with budget("weather_agent") as b:                    # timeout + token cap
        emit(s, "weather_agent", "started", host="L3")
        tr: ToolResult = await tools.weather(region=..., as_of=s["request"].get("as_of"))   # HTTP
        ev = apply_staleness(tr.evidence)
        sig = await narrate("P4", ev, s["portfolio"])     # llm.chat("narrator", schema=AgentSignal)
        if not sig: sig = code_fallback_signal(ev)        # template sentence, never crash
        emit(s, "weather_agent", "degraded" if any(e.degraded for e in ev) else "finished",
             evidence_ids=[e.id for e in ev], latency_ms=b.ms)
        return {"evidence": [e.model_dump() for e in ev], "signals": [sig.model_dump()]}
```
Agent → tool mapping: sentiment_agent → ingestion `/news`, then sentiment `/sentiment/score` (P3 second opinion runs inside the sentiment service). weather_agent → ingestion `/weather/features` + P4. agri_agent → agri `/agri_signal` + P5. macro_agent → ingestion `/macro/features` + P6. analog_agent → vectordb `/find_analogs` + P7. exposure_agent → quant `/exposure` (code summary, no LLM).

## 3. Staleness factor (`staleness.py`)
```python
HALF_LIFE_S = {"prices": 86_400, "news": 43_200, "sentiment": 43_200, "weather": 21_600,
               "macro": 7*86_400, "agri": 16*86_400, "exposure": 86_400,
               "analogs": None, "event_study": None, "risk": 86_400, "hedge": 86_400, "scenario": None}
def staleness_factor(tool: str, freshness_s: int | None) -> float:
    hl = HALF_LIFE_S.get(tool)
    if hl is None or freshness_s is None: return 1.0
    return max(0.3, 0.5 ** (freshness_s / hl))
```
Effective confidence = `evidence.confidence × staleness_factor`. Store the factor in `evidence.staleness_factor` (01 §5) so the UI can draw a freshness badge (green above 0.8, amber from 0.5 to 0.8, red below 0.5).

## 4. Budgets (`budget.py`)
```python
AGENT_BUDGET = {   # timeout_s, max_tokens for the LLM part
  "parse_intent": (8, 300), "sentiment_agent": (15, 0), "weather_agent": (12, 300),
  "agri_agent": (12, 300), "macro_agent": (12, 300), "analog_agent": (12, 400),
  "exposure_agent": (8, 0), "quant_agent": (20, 0), "planner": (10, 300),
  "synthesizer": (40, 900), "red_team": (20, 400), "validator": (3, 0)}
RUN_DEADLINE_S = 90;  MAX_STEPS = 6     # planner loop cap
```
When a budget times out, the node emits `degraded`, returns a code fallback, and the run continues. Tokens used per node are recorded in `AgentEvent.tokens_*`, and `FinalAnswer.latency_ms` holds per-stage times for the latency panel.

## 5. Time machine (`as_of`)
If `QueryRequest.as_of` is set, it is forwarded to every tool call. Tools filter their data to timestamps ≤ `as_of` (ingestion and replay cache), vectordb excludes events whose `start_date` ≥ `as_of`, and news is limited to `published_at` ≤ `as_of`. The backtest (12) uses this exact path.

## 6. Numbers Ledger validator (`nodes/validator.py`, code only)
1. **Extract** numbers from `draft` with a regex: `(?<![\w.])[-+−]?₹?\d[\d,]*(?:\.\d+)?\s?(?:%|σ|bps|bp|cr|crore|lakh|x)?`. Keep the sentence and any `[ev_...]` tags in that sentence.
2. **Ignore**: years (1900–2100) next to dates, day counts equal to `intent.horizon_days`, list markers (`1)`, `2.`), numbers that appear in the user query, and citation IDs.
3. **Normalise**: remove `₹`, `,` and the space. Convert `%` to both `x` and `x/100`, convert `bps` to `x/10000`, and convert crore and lakh to INR.
4. **Evidence pool**: flatten every numeric leaf of each cited evidence's `value` (plus `confidence`). Also add derived forms: ×100, abs, and rounding to 0, 1 and 2 decimals.
5. **Match**: a number matches if `|a-b| ≤ max(0.005·|b|, 0.051)` against the pool of the evidence **cited in the same sentence**. If the sentence has no citation, search all evidence, and a match there still counts as `uncited`, which is flagged.
6. **Action**: if everything matches, `pass`. If 1–2 numbers are unmatched or uncited, `flagged` (wrap them as `⚠️{n}` in `answer_markdown`). If more than 2 are unmatched, `stripped` (remove those sentences and add a note at the end: "N unsupported figures removed").
7. Output a `ValidatorReport`, and emit a `validator` event with the counts.

Unit tests: "Brent rose 4.2% [ev_macro_001]" with `brent_chg_5d: 0.042` gives pass. "about 7%" with no evidence gives stripped or flagged.

## 7. Persistence and resume
- **Ledger** (`data/ledger.db`, sqlite3/aiosqlite) with tables: `runs(run_id, created_at, query, status, final_json)`, `events(run_id, seq, json)`, `evidence(id, run_id, tool, json)`.
- **Checkpointer**: `langgraph-checkpoint-sqlite` `AsyncSqliteSaver.from_conn_string("data/checkpoints.db")`, with `thread_id = run_id`. If a laptop drops out mid-run, the run ends `failed`. `POST /runs/{id}/resume` calls `graph.ainvoke(None, config)`, which continues from the last completed node, so finished agents are not re-run.

## 8. API

| Method | Path | Body → Response |
|---|---|---|
| POST | `/query` | `QueryRequest` → `QueryAccepted`. The run starts as a background task |
| WS | `/ws/{run_id}` | streams `{"type":"event"}`, then `{"type":"final"}` (01 §8). Late subscribers get past events replayed first |
| GET | `/runs?limit=20` | list of `{run_id, query, created_at, status, confidence}` |
| GET | `/runs/{id}` | `{"final": FinalAnswer, "events": [AgentEvent]}` |
| GET | `/runs/{id}/explain` | P10 over the stored log → `{"explanation_markdown", "steps":[...]}` |
| POST | `/runs/{id}/resume` | resumes from the checkpoint |
| GET | `/llm/quota` | quota snapshot + session usage totals (from 03) |
| GET | `/health/all` | aggregates every service `/health` (same as `check_health.py --json`) |
| GET/POST | `/portfolio`, `/portfolio/upload` (CSV: `ticker,qty,avg_price,sector`) | `Portfolio` |
| GET | `/portfolio/{id}` | `Portfolio` (the monitor (11) reads `demo`) |
| WS | `/ws/activity` | every `AgentEvent` of every run (used by the pet, 14) |
| GET | `/runs/{id}/events.jsonl` | lines `{"t_ms": ms since run start, "msg": {"type":"event"\|"final","data":...}}`, so the frontend's mock mode can replay real runs (13) |
| POST | `/tools/scenario` | proxy to quant `/scenario` (06) for the what-if sliders → `ToolResult` |
| GET | `/backtest/scoreboard` | serves `data/backtest/scoreboard.json`, or `{"status":"not_run"}` (12) |
| * | `/paper/*` | paper-trading router from 12 §B3, mounted here |

The `parse_intent` finished event must put the Intent in `meta.intent`. `llm_usage.providers` follows the shape in 01 §8.

## 9. Build prompt (paste into a coding LLM)

```
Build services/orchestrator: a FastAPI + LangGraph (langgraph>=0.2, langgraph-checkpoint-sqlite)
service on port 8000. Use copilot_common (models, ids, settings, service_base.create_service_app)
and copilot_llm (llm.chat). Python 3.11, everything async.

Step 1  state.py — RunState exactly as in spec §2 (operator.add reducers for evidence/signals/errors).
Step 2  events.py — EventBus: emit(run_id, node, status, **kw) builds AgentEvent with per-run seq,
        stores to ledger, and pushes to all asyncio.Queue subscribers of that run_id.
        subscribe(run_id) yields past events first, then live ones.
Step 3  ledger.py — aiosqlite tables runs/events/evidence (spec §7) with save/load helpers.
Step 4  tools_client.py — one async function per tool returning ToolResult:
        news, weather, macro (INGEST_URL), sentiment (SENTIMENT_URL /sentiment/score),
        agri (AGRI_URL /agri_signal), analogs (VECTOR_URL /find_analogs),
        exposure, risk(/var_montecarlo), hedge(/hedge_proposals), event_study, scenario (QUANT_URL).
        Shared httpx.AsyncClient(timeout per budget), header X-Run-Id, body includes as_of and chaos.
        On connection error/timeout → load fixture copilot_common/fixtures/<service>/<endpoint>.json and
        mark every evidence degraded=True, degraded_reason="service_unreachable".
        Re-number evidence IDs with EvidenceCounter(run_id) so ids are unique per run.
Step 5  staleness.py and budget.py exactly as spec §3-4 (budget is an async context manager using
        asyncio.timeout).
Step 6  router.py — select_agents(intent: Intent, portfolio) -> list[str] implementing the table in §2.
Step 7  nodes: parse_intent (P1, schema=Intent, fallback: keyword rules if LLM fails),
        6 agent nodes following the agent node pattern in §2, join (dedupe evidence, emit counts),
        planner (P2 with tools schema, max 2 extra tool calls, MAX_STEPS=6, only if LLM_MODE!=local
        or the intent has needs_tools not covered), quant_agent (parallel asyncio.gather of
        risk/hedge/scenario as rules in §2), synthesizer (P8, role "synthesizer", passes lang),
        red_team (P9, schema=RedTeamReport), validator (algorithm §6, pure python),
        explain (P10 over ledger rows of a referenced run).
        Prompts are loaded from prompts/P*.txt (content from 05_RUNTIME_PROMPTS.md).
Step 8  graph.py — build_graph(checkpointer) wiring §2 with Send fan-out; compile with checkpointer.
Step 9  app.py — create_service_app("orchestrator"); endpoints in §8. /query: new_run_id, save run,
        asyncio.create_task(run_graph(...)) with RUN_DEADLINE_S, return QueryAccepted.
        run_graph builds the FinalAnswer (include llm_usage from copilot_llm usage_for(run_id) and
        per-stage latency_ms) and emits {"type":"final"}. Set chaos contextvars from request.chaos.
        Also add: WS /ws/activity (EventBus fan-out of all runs), GET /runs/{id}/events.jsonl,
        GET /portfolio/{id}, POST /tools/scenario (httpx proxy to QUANT_URL/scenario),
        GET /backtest/scoreboard, and mount the /paper router from services/orchestrator/paper (doc 12).
        Put the Intent into meta.intent on the parse_intent finished event.
Step 10 tests/: router table; validator cases (pass / flagged / stripped); staleness factor values;
        full graph run with MOCK=1 producing a valid FinalAnswer with ≥6 agents' events;
        parallelism test: 6 mock agents each sleeping 1 s finish in < 2 s total.
Output complete files.
[PASTE 01_CONTRACTS.md §5,§8 AND THIS FILE §2-§8]
```

## 10. Example — final message on the WebSocket (abridged)
```json
{"type": "final", "data": {
  "run_id": "run_20261003141502_a91f",
  "query": "Cyclone heading to Odisha — what happens to my portfolio this week?",
  "intent": {"intent":"event_impact","event_type":"cyclone","region":"Odisha","tickers":[],"asset_classes":["equity"],"horizon_days":5,"references_portfolio":true,"needs_tools":["weather","analogs","exposure","risk"]},
  "bottom_line": "A severe cyclone is forecast to reach the Odisha coast in 2 days [ev_weather_001]. Your largest exposure is Coal India and NTPC (31% of the portfolio [ev_exposure_001]); past cyclones moved similar names by −4.1% to +1.2% over 5 days [ev_analogs_001].",
  "holdings_impact": [{"ticker":"COALINDIA.NS","impact":"negative","range":"-4.1% to +1.2% (5d)","evidence_ids":["ev_analogs_001","ev_exposure_001"]}],
  "hedges": [{"hedge_id":"h1","instrument":"NIFTY OCT FUT short","underlying":"^NSEI","side":"sell","quantity":1,"unit":"lots","hedge_ratio":0.42,"est_cost_inr":1850,"rationale":"beta hedge of energy/utility sleeve","sizing_method":"beta","evidence_ids":["ev_hedge_001"]}],
  "confidence": "medium",
  "what_could_be_wrong": ["Only 4 analogs matched [ev_analogs_001]", "Weather feed is 6h old [ev_weather_001]"],
  "red_team": {"reasons":["...","...","..."],"verdict":"proceed with caution","verdict_reason":"Analog sample is small."},
  "validator": {"numbers_found":9,"numbers_matched":9,"unmatched":[],"action":"pass"},
  "signals": [{"agent":"weather_agent","signal":"bearish","summary":"...","evidence_ids":["ev_weather_001"],"confidence":0.62,"degraded":true}],
  "evidence": [{"id":"ev_weather_001","tool":"weather","...":"..."}],
  "answer_markdown": "### Bottom line\n...",
  "lang": "en",
  "llm_usage": {"cloud_calls":1,"local_calls":8,"cache_hits":0,"saved_calls":8},
  "latency_ms": {"parse_intent":610,"fanout":4200,"quant_agent":900,"synthesizer":3100,"red_team":1800,"validator":12,"total":10700}
}}
```

## 11. Mock mode
With `MOCK=1`, every tool call returns fixtures and `copilot_llm` returns canned LLM output, so a full run finishes in about 1 s with every event emitted. Use this so the frontend (13) can be built on day 0. Add `MOCK_DELAY_MS=800` to space events out so the live graph animation is visible.

## 12. Acceptance checklist
- [ ] A MOCK=1 run produces a schema-valid `FinalAnswer` and at least 14 events
- [ ] The 6 agents run in parallel: fan-out wall time is close to the slowest agent, not the sum (shown in latency_ms)
- [ ] Killing L2 mid-run marks its agents `degraded` while the answer still arrives
- [ ] `POST /runs/{id}/resume` after a forced failure does not re-run finished agents
- [ ] The validator strips an injected fake number
- [ ] `as_of=2021-08-25` produces no evidence dated after that day
- [ ] The hurricane query and the Odisha cyclone query run end to end by H14

## 13. Integration hooks
- The frontend (13) uses `/query`, `/ws/{id}`, `/runs`, `/runs/{id}/explain`, `/llm/quota` and `/health/all`.
- The monitor (11) can start a run with `POST /query`, which powers the "analyse this alert" deep link.
- The backtest (12) calls `run_graph` directly (an importable function) with `as_of`.

## 14. Sources
- LangGraph Send / map-reduce: https://docs.langchain.com/oss/python/langgraph/use-graph-api
- LangGraph persistence and checkpointers: https://docs.langchain.com/oss/python/langgraph/persistence
- Send concurrency caveat: https://forum.langchain.com/t/best-practices-for-parallel-nodes-fanouts/1900
- FastAPI WebSockets: https://fastapi.tiangolo.com/advanced/websockets/
