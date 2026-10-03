# 13 — Frontend Terminal (Next.js)

| | |
|---|---|
| **Owner / Laptop** | Frontend dev, **L3 "Edge & UI"**, port **3000** |
| **Depends on** | [01_CONTRACTS.md](01_CONTRACTS.md) (generated `contracts.ts` + fixtures), orchestrator WS (04), monitor WS (11), quant `/scenario` and `/exposure` (06), backtest JSON (12), every service's `/health` |
| **Provides** | The user-facing terminal: query, live agent graph, cited answer, evidence drill-down, health, latency, backtest, portfolio, paper trades, settings, what-if sliders |
| **Can start** | Hour 1, fully against mocks (`NEXT_PUBLIC_MOCK=1`). No backend is needed until about H12 |

---

## 1. Aesthetic direction (pick it on day 0 and keep to it)

**"Mumbai trading desk at 2 a.m." — a dense, amber-on-ink terminal, not a SaaS dashboard.**

- **Palette tokens** (CSS variables on `:root`; a light theme is optional):
  `--ink:#0b0f14` (background), `--panel:#121821`, `--line:#1f2a36`, `--amber:#ffb000` (primary and accent), `--saffron:#ff7a1a` (alerts), `--mint:#3ddc97` (bullish and ok), `--rose:#ff4d6d` (bearish and down), `--muted:#7a8a9c`, `--text:#e6edf3`.
- **Type:** `JetBrains Mono` for numbers, tickers and citations, and `Inter Tight` for prose. Both come from Google Fonts. Every number uses tabular figures (`font-variant-numeric: tabular-nums`).
- **Layout:** a 12-column grid with hairline borders (`1px var(--line)`) and no rounded cards. Panels carry a small uppercase label at the top left, for example `▍AGENT GRAPH · run_2026…a91f`.
- **Signature details:** a blinking amber cursor in the query box, and a ticker-tape strip at the top showing holdings with Δ%. Each evidence citation renders as a chip like `[ev_weather_001]`, coloured by tool. Degraded items get a diagonal-hatch background. The live graph's edges "pulse" amber while a node runs.
- **Motion:** only for state changes (a node starts or finishes, an alert arrives). Respect `prefers-reduced-motion`.

---

## 2. Folder layout

```
apps/terminal/
├── app/
│   ├── layout.tsx              # fonts, theme, TickerTape, NavRail, AlertToaster
│   ├── page.tsx                # "/" terminal
│   ├── run/[runId]/page.tsx    # replay of a stored run (GET /runs/{id})
│   ├── run/new/page.tsx        # deeplink target: ?q=... → auto-submit
│   ├── evidence/[id]/page.tsx  # full evidence view
│   ├── health/page.tsx
│   ├── latency/page.tsx
│   ├── backtest/page.tsx
│   ├── portfolio/page.tsx
│   ├── paper/page.tsx
│   └── settings/page.tsx
├── components/
│   ├── QueryBox.tsx            ├── IntentDecomposition.tsx
│   ├── AgentGraph.tsx          ├── AgentNode.tsx
│   ├── AnswerPanel.tsx         ├── CitationChip.tsx
│   ├── EvidenceDrawer.tsx      ├── EvidenceCard.tsx
│   ├── FreshnessBadge.tsx      ├── DegradedBanner.tsx
│   ├── RedTeamCard.tsx         ├── ValidatorBadge.tsx
│   ├── HedgeTable.tsx          ├── WhatIfSliders.tsx
│   ├── SectorHeatmap.tsx       ├── PriceChart.tsx
│   ├── WeatherChart.tsx        ├── QuotaMeter.tsx
│   ├── LatencyWaterfall.tsx    ├── HealthGrid.tsx
│   ├── CalibrationPlot.tsx     ├── Scoreboard.tsx
│   ├── TickerTape.tsx          ├── AlertToaster.tsx
│   └── NavRail.tsx
├── lib/
│   ├── contracts.ts            # GENERATED from copilot_common (never hand-edit)
│   ├── schema.json             # GENERATED
│   ├── config.ts               # URLs from NEXT_PUBLIC_* env
│   ├── api.ts                  # fetch wrappers (real | mock)
│   ├── ws.ts                   # useRunStream(runId), useAlerts()
│   ├── mock/replay.ts          # replays a recorded event stream with original timing
│   ├── graphLayout.ts          # fixed node positions for the 13 nodes
│   ├── citations.ts            # parse "[ev_xxx_001]" → chips
│   └── store.ts                # zustand: settings, current run, evidence map
├── public/mock/
│   ├── run_hurricane.events.jsonl   # recorded {"t_ms":..,"msg":{...}} lines
│   ├── run_cyclone.events.jsonl
│   ├── health.json  backtest.json  portfolio_demo.json  scenario.json  exposure.json
├── .env.local.example
└── package.json
```

**Stack:** Next.js 15 (App Router) + TypeScript + Tailwind CSS v4, with `@xyflow/react` for the graph. `lightweight-charts` (TradingView) draws price candles and `recharts` draws the weather, calibration and latency charts. State lives in `zustand`, CSV import uses `papaparse`, and the drawer and dialog come from `@radix-ui/react-dialog`.

---

## 3. Environment

```dotenv
# apps/terminal/.env.local
NEXT_PUBLIC_MOCK=1
NEXT_PUBLIC_ORCH_URL=http://192.168.43.101:8000
NEXT_PUBLIC_ORCH_WS=ws://192.168.43.101:8000
NEXT_PUBLIC_MONITOR_WS=ws://192.168.43.103:8202
NEXT_PUBLIC_QUANT_URL=http://192.168.43.102:8101
NEXT_PUBLIC_HEALTH_URLS=orchestrator=http://192.168.43.101:8000,quant=http://192.168.43.102:8101,sentiment=http://192.168.43.102:8102,agri=http://192.168.43.102:8103,vectordb=http://192.168.43.102:8104,ingestion=http://192.168.43.103:8201,monitor=http://192.168.43.103:8202
```

The browser calls services directly. Every service has CORS `*` (see 01 §7).

---

## 4. Build prompts (paste one at a time into a coding LLM)

### Prompt 13-A: Scaffold, contracts and mock layer
```
Build a Next.js 15 App Router + TypeScript + Tailwind v4 app at apps/terminal.

1. `npx create-next-app@latest apps/terminal --ts --tailwind --app --eslint --src-dir=false`.
   Install: @xyflow/react lightweight-charts recharts zustand papaparse @radix-ui/react-dialog clsx.
2. Add scripts in package.json:
   "gen:contracts": "python -m copilot_common.export_schema > lib/schema.json && npx json-schema-to-typescript lib/schema.json > lib/contracts.ts"
   Commit the generated lib/contracts.ts. Import all types (Evidence, AgentEvent, FinalAnswer, Intent,
   Alert, Health, HedgeProposal, QueryRequest, QueryAccepted, ChaosFlags, Portfolio) ONLY from lib/contracts.ts.
3. lib/config.ts: read NEXT_PUBLIC_* vars (see section 3), export MOCK boolean and a parsed HEALTH_URLS map.
4. lib/mock/replay.ts: export `replayStream(file: string, onMsg: (m) => void, speed = 1): () => void`.
   It fetches /mock/<file>.events.jsonl, where each line is {"t_ms": number, "msg": {"type":"event"|"final", "data": ...}},
   and calls onMsg at the recorded offsets (setTimeout chain). It returns a cancel fn.
   Choose the file by keyword: query contains "cyclone|monsoon|odisha|kharif" → run_cyclone, else run_hurricane.
5. lib/api.ts: postQuery(req: QueryRequest): Promise<QueryAccepted>; getRun(id); getHealth(name,url);
   postScenario(body); postExposure(body); getBacktest(). In MOCK mode each returns the matching
   /public/mock/*.json (postQuery returns {run_id:"run_mock_<ts>", ws_url:"mock://<file>"}).
6. lib/ws.ts: hook useRunStream(runId, wsUrl) → {events: AgentEvent[], final: FinalAnswer|null, status}.
   For a real URL, open WebSocket(`${ORCH_WS}/ws/${runId}`) and parse {"type":"event"|"final","data"}.
   For a mock:// URL, use replayStream. Keep events sorted by seq; dedupe by seq.
   Hook useAlerts() → connects NEXT_PUBLIC_MONITOR_WS + "/ws/alerts", with exponential reconnect (1s→30s);
   in MOCK mode emit one fixture alert 20 s after load.
7. lib/store.ts (zustand + persist to localStorage, wrapped in try/catch): settings {llmMode:"local"|"boost"|"auto",
   lang:"en"|"hi"|"hinglish", chaos: ChaosFlags, asOf: string|null}; evidenceById: Record<string, Evidence>;
   currentRunId.
8. app/globals.css: define the tokens from the Aesthetic section (--ink, --panel, --line, --amber, --saffron,
   --mint, --rose, --muted, --text) and load JetBrains Mono + Inter Tight via next/font/google.
9. Create public/mock/run_hurricane.events.jsonl with ≥ 25 lines that exactly follow the sequence in
   docs/15_INTEGRATION_AND_DEMO.md §4.1 (parse_intent → router → 6 parallel agents → join → quant_agent →
   synthesizer → red_team → validator → final). Include one "degraded" event, and give events realistic t_ms gaps.
Output all files completely.
```

### Prompt 13-B: Terminal page with live agent graph
```
Using lib/ws.ts and lib/contracts.ts, build app/page.tsx (the terminal):

Layout (12-col grid, hairline borders, no rounded cards):
  row 1: <TickerTape/> (holdings with Δ%, from portfolio_demo + latest prices)
  row 2: <QueryBox/> full width  + small <QuotaMeter/> at right
  row 3: left 7 cols <AgentGraph/>, right 5 cols <IntentDecomposition/> above <LatencyWaterfall compact/>
  row 4: left 8 cols <AnswerPanel/>, right 4 cols <RedTeamCard/> + <ValidatorBadge/> + <HedgeTable/>
  row 5: <WhatIfSliders/> | <SectorHeatmap/> | <PriceChart/>

1. QueryBox: textarea + blinking amber cursor; Enter submits. It sends a QueryRequest built from the store
   (llm_mode, lang, chaos, as_of). Show example chips: "Cat 4 hurricane heading to Louisiana — impact on my portfolio?",
   "Cyclone in Bay of Bengal + weak monsoon — what happens to my FMCG and agri stocks?".
2. AgentGraph (@xyflow/react): 13 fixed nodes from lib/graphLayout.ts
   (parse_intent, router, sentiment_agent, weather_agent, agri_agent, macro_agent, analog_agent, exposure_agent,
   join, quant_agent, synthesizer, red_team, validator). Router→6 agents fan out; the 6 agents → join.
   Node colour by latest status: queued=muted, started/progress=amber (pulsing border), finished=mint,
   degraded=saffron with hatch, failed=rose, skipped=dim+dashed. The node subtitle shows model@host and latency_ms.
   Animate edges into a node while it is "started". Clicking a node opens a popover listing its events + evidence chips.
3. IntentDecomposition: when the parse_intent "finished" event arrives (or final.intent exists), render the Intent
   as labelled rows: intent, event_type, region, tickers (chips), horizon_days, needs_tools (chips; tools the
   router skipped are greyed out).
4. AnswerPanel: render final.answer_markdown. lib/citations.ts replaces every /\[(ev_[a-z_]+_\d{3})\]/ with
   <CitationChip id=…/>. A chip is coloured by tool; clicking it opens <EvidenceDrawer id/>. Above the answer:
   bottom_line in large type and a confidence pill (low/medium/high). If any final.evidence[].degraded, show
   <DegradedBanner/> listing degraded tools + reasons.
5. EvidenceDrawer (radix dialog, right side): EvidenceCard with tool, source (link source_url), as_of,
   <FreshnessBadge freshness_s/>, confidence bar, degraded_reason, latency_ms, model_version, and value as a
   pretty JSON tree plus a tool-specific mini view (prices→sparkline, weather→bars, analogs→p10/median/p90 range bar,
   risk→VaR number + histogram). There is a "Open full page" link to /evidence/[id].
6. FreshnessBadge thresholds: <15 min "LIVE" mint; <6 h "FRESH" amber; <3 d "STALE" saffron; else "OLD" rose.
7. QuotaMeter: reads final.llm_usage → "cloud 2 · local 7 · saved 5" and providers; plus a per-provider bar
   (requests today / daily limit) using llm_usage.providers[p].{used,limit}. Show "LOCAL ONLY" if llm_mode=local.
8. RedTeamCard: 3 reasons with chips + verdict pill (proceed=mint, caution=amber, do not act=rose).
   ValidatorBadge: "Numbers Ledger 14/14 ✓" or "12/14 · 2 flagged" with a tooltip listing unmatched.
   HedgeTable: instrument, side, quantity+unit, hedge_ratio, est_cost_inr, sizing_method, and an
   "Approve → paper" button: POST orchestrator /paper/propose {run_id, hedge_id}, then on confirm
   POST /paper/approve {proposal_id, decision:"approve", approved_by} (see 12 §B3).
Use only types from lib/contracts.ts.
```

### Prompt 13-C: What-if, heatmap and charts
```
1. WhatIfSliders: sliders for crude_pct (-30..+30), usd_inr_pct (-5..+5), nifty_pct (-15..+15),
   monsoon_rain_pct (-40..+20), repo_bps (-50..+50). Debounce 300 ms → POST {ORCH_URL}/tools/scenario (the orchestrator
   proxies to quant /scenario, see 06) with
   {"portfolio": <Portfolio>, "shocks": {"crude": 10, "usd_inr": 2, "nifty": -3, "monsoon_rain": -20, "repo_bps": 25}}
   (shock values are PERCENT POINTS, except repo_bps which is basis points; keys exactly as in 06 ScenarioReq).
   Response is ToolResult; read evidence[0].value {pnl_inr, pnl_pct, by_ticker}. Show the P&L as large tabular
   number (mint/rose) + per-ticker bars. Tag the result "scenario · computed by quant engine [ev_scenario_xxx]".
2. SectorHeatmap: read the exposure evidence from the current FinalAnswer (or POST {QUANT_URL}/exposure with the portfolio) → value.heatmap (rows = sectors, cols = factors
   ["weather","agri","crude","rates","usd_inr"]; cell = sensitivity −1..1). Render a CSS grid with a diverging
   rose↔ink↔mint scale, each cell labelled with its value; the row label shows the sector weight %.
3. PriceChart: lightweight-charts candlestick for the selected ticker from prices evidence (value.rows),
   with vertical markers for events (analog dates) when available.
4. WeatherChart (recharts): rain_anomaly_pct and max_temp_c bars per region from weather evidence.
5. LatencyWaterfall: from final.latency_ms (and event latencies) draw horizontal bars per stage
   (ingest, embed, retrieval, each agent, quant, synthesizer, red_team, validator, total).
```

### Prompt 13-D: Secondary pages
```
1. /health (HealthGrid): poll each URL in HEALTH_URLS + "/health" every 5 s (timeout 2 s). There is one tile per
   service showing status (ok/degraded/down), host, mock flag, uptime, deps map, models list. Group the tiles by
   laptop L1/L2/L3. Show a red "unreachable" state on timeout, with the hint "check firewall / 0.0.0.0 / same Wi-Fi".
2. /latency: list the last 20 runs (GET /runs?limit=20), a LatencyWaterfall per selected run, and a
   "news index latency" chart (indexed_at − ingested_at, from vectordb GET {VECTOR_URL}/latency, see 08) with median and p95.
3. /backtest: GET orchestrator /backtest/scoreboard (12 §A8 schema; {"status":"not_run"} → empty state) → Scoreboard table (event, date, predicted direction,
   actual, hit ✓/✗, abs error vs baselines price-only & sentiment-only). Misses are shown, never hidden. Add a
   CalibrationPlot (recharts scatter + y=x line: predicted confidence bucket vs realized hit rate).
4. /portfolio: an editable table (ticker, qty, avg_price, sector) + CSV upload (papaparse; required columns
   ticker,qty[,avg_price,sector]; tickers without a suffix get ".NS" appended, with a warning). Save via orchestrator
   POST /portfolio (JSON Portfolio) or POST /portfolio/upload (CSV), see 04. Validate with the Portfolio type.
5. /paper: pending proposals from GET /paper/history (status=pending) with Approve/Reject buttons →
   POST /paper/approve {proposal_id, decision:"approve"|"reject", approved_by}; open positions from
   GET /paper/positions?status=open show entry price, current mark and P&L (see 12 §B3).
6. /settings: LLM mode segmented control (local | boost | auto) with a note "boost uses Groq free tier,
   falls back to local automatically"; language (en | hi | hinglish); chaos toggles bound to ChaosFlags
   (weather_down, force_rate_limit, agri_raster_missing, vector_down, slow_network_ms slider);
   time-machine date picker (as_of). Everything is stored in zustand and sent with each QueryRequest.
7. /run/new?q=...: read q, prefill QueryBox and auto-submit (the deeplink target used by alerts and the pet).
   /run/[runId]: load GET /runs/{id} and replay its events at 4× speed through the same AgentGraph.
8. AlertToaster (in layout): useAlerts(); tier 1 → small amber dot on NavRail; tier 2/3 → toast with
   headline, tickers, confidence and an "Analyze" button → router.push(alert.deeplink path).
```

---

## 5. Example data the UI must handle

The intent decomposition comes from the `parse_intent` finished event (`meta.intent`) or `final.intent`:
```json
{"intent":"event_impact","event_type":"hurricane","region":"Gulf of Mexico","tickers":["RELIANCE.NS","ONGC.NS","NG=F"],
 "asset_classes":["equity","commodity"],"horizon_days":5,"references_portfolio":true,
 "needs_tools":["weather","analogs","exposure","sentiment","risk","hedge"]}
```

`final.llm_usage`:
```json
{"cloud_calls":1,"local_calls":8,"saved_calls":6,
 "providers":{"groq":{"used":37,"limit":1000,"model":"openai/gpt-oss-120b"},"ollama@L1":{"used":5},"ollama@L2":{"used":3}}}
```

Scenario response (`evidence[0].value`):
```json
{"shocks":{"crude":10,"usd_inr":2},"pnl_inr":-18450.0,"pnl_pct":-0.0123,
 "by_ticker":{"RELIANCE.NS":-9200.0,"ONGC.NS":6100.0,"ITC.NS":-1500.0}}
```

Exposure heatmap (`evidence[0].value.heatmap`). The shape is `{rows, cols, values}` as defined in 06. `row_weights` is optional; if it's missing, take the weights from `value.by_sector`:
```json
{"rows":["Energy","FMCG","Agri","Banks"],"cols":["weather","agri","crude","rates","usd_inr"],
 "values":[[0.6,0.1,0.8,-0.2,0.3],[0.3,0.5,-0.2,-0.1,-0.1],[0.7,0.9,-0.1,0.0,0.1],[0.0,0.1,-0.1,0.6,-0.2]],
 "row_weights":{"Energy":0.38,"FMCG":0.27,"Agri":0.15,"Banks":0.20}}
```

Mock stream line (`public/mock/run_hurricane.events.jsonl`):
```json
{"t_ms":0,"msg":{"type":"event","data":{"run_id":"run_mock","seq":1,"node":"parse_intent","status":"started","ts":"2026-10-03T08:45:02Z","model":"qwen3:4b-instruct","provider":"ollama@L1","host":"L1"}}}
```

---

## 6. Mock mode

- With `NEXT_PUBLIC_MOCK=1`, the app needs no backend: queries replay the recorded `.events.jsonl`, and all panels read from `public/mock/*.json`.
- **Record real streams later.** Orchestrator `GET /runs/{id}/events.jsonl` (04) exports a real run with timings. Drop the file into `public/mock/` so the demo can replay a real run with no network.
- A small "MOCK" tag in the nav rail must always show when mock mode is on, so it's never mistaken for live data.

## 7. Acceptance checklist
- [ ] `npm run dev` with `NEXT_PUBLIC_MOCK=1`: the hurricane query animates all 13 nodes, including at least one parallel batch and one degraded node, and ends with a cited answer
- [ ] Every `[ev_…]` chip opens the drawer, and unknown IDs show "evidence not found" rather than crashing
- [ ] `/health` shows all 7 services. Killing one turns its tile red within 5 s
- [ ] The what-if sliders update P&L within 1 s (live quant) and show the scenario evidence ID
- [ ] The settings toggles appear in the outgoing `QueryRequest` (check the network tab)
- [ ] CSV upload of `data/portfolio_demo.csv` round-trips
- [ ] Tier-2 alert toast → "Analyze" opens `/run/new?q=…` and auto-runs
- [ ] At 375 px wide, the panels stack with no horizontal scroll
- [ ] `lib/contracts.ts` is generated and not hand-edited, and `tsc --noEmit` passes

## 8. Integration hooks
| Needs from | Endpoint | Doc |
|---|---|---|
| Orchestrator | `POST /query`, `WS /ws/{run_id}`, `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/events.jsonl`, `GET/POST /portfolio`, `POST /portfolio/upload`, `POST /tools/scenario`, `GET /llm/quota`, `GET /health/all`, `/paper/propose`, `/paper/approve`, `/paper/positions`, `/paper/history`, `GET /backtest/scoreboard` | 04, 12 |
| Monitor | `WS /ws/alerts`, `POST /alerts/{id}/ack` | 11 |
| Quant | `POST /exposure` (direct, optional) | 06 |
| Vector DB | `GET /latency` | 08 |
| All | `GET /health` | 01 §7 |

## 9. Sources
- React Flow (`@xyflow/react`): https://reactflow.dev/learn
- TradingView lightweight-charts: https://tradingview.github.io/lightweight-charts/
- Next.js App Router: https://nextjs.org/docs/app
- json-schema-to-typescript: https://github.com/bcherny/json-schema-to-typescript
