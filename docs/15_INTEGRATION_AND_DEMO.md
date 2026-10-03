# 15 — Integration, Testing and Demo

| | |
|---|---|
| **Owner / Laptop** | Team lead (L1), with every module owner on call |
| **Depends on** | All modules 01–14 |
| **Provides** | Integration order, end-to-end test scripts with expected traces, chaos matrix, demo script, judge Q&A, backup plan |

---

## 1. Integration order (three rings, never skip one)

| Ring | What runs | How | Goal |
|---|---|---|---|
| **R0 Mock** | Everything with `MOCK=1` (services) and `NEXT_PUBLIC_MOCK=1` (UI) | Any laptop | The UI, graph and contracts agree. Every fixture validates against `copilot_common` |
| **R1 Single laptop** | All services on L1, all hosts `127.0.0.1`, `LLM_MODE=local` | `infra/run_all_local.ps1` | The real pipeline works end to end, with no LAN issues |
| **R2 Three laptops** | Services on their own laptops (01 §1), real `.env` hosts | `infra/check_health.py` first | Real parallelism across GPUs, and failover when a laptop drops |

Enable real services **one at a time** inside a ring, keeping the others on `MOCK=1`, in this order: ingestion → quant → sentiment → vectordb → agri → monitor. When a run breaks, the last service you switched on is the suspect.

`infra/run_all_local.ps1` (R1) starts the following in separate terminals: `docker compose -f infra/docker-compose.yml up -d`, then `ollama serve`, then each service with `uvicorn <svc>.main:app --host 0.0.0.0 --port <port>`, then `npm run dev` in `apps/terminal`.

---

## 2. Milestone checklist by hour

| Hour | Milestone (owner) | Done when |
|---|---|---|
| H0–1 | Contracts merged (01). Network smoke test (02). Ollama `qwen3:4b-instruct` and `gemma3:4b` answer a tool-call test. One Groq call works | `pytest packages/copilot_common` is green. `check_health.py` pings all 3 IPs |
| H1–4 | Every service skeleton has `/health` and mock fixtures. The UI runs on the mock stream | The R0 hurricane replay animates in the UI |
| H4–8 | Real ingestion (prices, RSS, Open-Meteo), quant engines with tests, Weaviate seeded, FinBERT scoring, agri model v0 (NDVI anomaly only) | Each service's own acceptance checklist passes |
| H8–12 | LangGraph with real fan-out, using local LLMs only | R1: the hurricane query returns a `FinalAnswer` |
| **H12–14** | **R1 end to end for both scripted queries (§4), and the validator passes** | Both traces match §4 |
| H14–17 | R2 across 3 laptops. Analog distributions, red team, backtest, alerts | Kill L2 and the run still finishes with degraded banners |
| H17–20 | Pet, paper trading, chaos toggles, what-if sliders, Groq boost mode | Chaos matrix (§5) all ✓ |
| H20–22 | Hardening, the disclaimer "Decision support, not trading signals", **code freeze** | 5 consecutive clean runs of each query |
| H22–24 | Record the backup video, rehearse 5×, prepare judge answers | §7 and §8 checklists done |

---

## 3. Golden test harness (`tests/e2e/run_golden.py`)

```
Build tests/e2e/run_golden.py:
1. Load tests/e2e/golden.yaml: list of {name, query, portfolio_csv, as_of, llm_mode, expect:{...}}.
2. For each: POST {ORCH_URL}/query, open WS /ws/{run_id}, collect all AgentEvents until "final" (timeout 120 s).
3. Assert:
   - node set ⊇ expect.nodes_finished; nodes in expect.nodes_skipped have status "skipped"
   - the 6 agent nodes' "started" timestamps fall within 1.5 s of each other (proves the parallel fan-out)
   - final validates as FinalAnswer; final.validator.action in expect.validator_allowed
   - every [ev_...] in answer_markdown exists in final.evidence
   - final.intent.event_type == expect.event_type; expect.tickers ⊆ tickers in holdings_impact
   - latency_ms.total < expect.max_latency_ms
4. Print a table: name | pass/fail | total ms | cloud calls | local calls | degraded tools.
5. Exit code != 0 on failure. Run it with CACHE_MODE=replay for a free, deterministic regression run.
```

---

## 4. The two scripted end-to-end queries

Demo portfolio (`data/portfolio_demo.csv`):
```csv
ticker,qty,avg_price,sector
RELIANCE.NS,120,2850,Energy
ONGC.NS,800,265,Energy
ITC.NS,900,455,FMCG
HINDUNILVR.NS,80,2480,FMCG
UPL.NS,300,560,Agri
DHANUKA.NS,150,1350,Agri
COROMANDEL.NS,120,1700,Agri
HDFCBANK.NS,200,1650,Banks
```

### 4.1 Query A: Gulf hurricane
> **"A Category 4 hurricane is heading toward the Louisiana refinery coast. What does it mean for my portfolio over the next week, and how should I hedge?"**

**Expected intent:** `event_impact · hurricane · Gulf of Mexico · horizon 5 · tickers [RELIANCE.NS, ONGC.NS, NG=F, CL=F]`

**Expected event trace** (order within the parallel block may vary):
```
seq  node             status     host  note
1    parse_intent     started    L1    qwen3:4b-instruct
2    parse_intent     finished   L1    ~0.6 s
3    router           finished   L1    selects weather, analogs, exposure, sentiment, macro; skips agri
4-9  weather_agent | analog_agent | exposure_agent | sentiment_agent | macro_agent  started (parallel)
10   agri_agent       skipped    —     not relevant to a Gulf hurricane
11-15 ...agents       finished   L2/L3 each with evidence ids (ev_weather_001, ev_analogs_001, ...)
16   join             finished   L1
17   quant_agent      started    L2    event_study + var_montecarlo + hedge_proposals
18   quant_agent      finished   L2    ev_risk_001, ev_hedge_001, ev_event_study_001
19   synthesizer      started    L1    qwen3:4b-instruct (local) or openai/gpt-oss-120b (boost)
20   synthesizer      finished
21   red_team         finished   L3    phi4-mini
22   validator        finished   L1    numbers 14/14
23   final
```

**Expected `FinalAnswer` shape** (the values are illustrative, and real numbers come from the tools):
```json
{
  "intent": {"intent":"event_impact","event_type":"hurricane","region":"Gulf of Mexico","horizon_days":5},
  "bottom_line": "Gulf refinery outages historically lift refining margins and US natgas; your RELIANCE position is the main beneficiary while ONGC is mildly positive [ev_analogs_001]. Portfolio 5-day VaR rises to ₹41,200 at 95% [ev_risk_001].",
  "holdings_impact": [
    {"ticker":"RELIANCE.NS","impact":"positive","range":"+0.8% to +4.1% (p10–p90, 5d)","evidence_ids":["ev_analogs_001"]},
    {"ticker":"ONGC.NS","impact":"mildly positive","range":"-0.5% to +2.9%","evidence_ids":["ev_analogs_001"]},
    {"ticker":"ITC.NS","impact":"neutral","range":"n/a","evidence_ids":["ev_exposure_001"]}
  ],
  "hedges": [{"hedge_id":"h_001","instrument":"NIFTY OCT FUT short","underlying":"^NSEI","side":"sell","quantity":1,"unit":"lots","hedge_ratio":0.35,"sizing_method":"beta","rationale":"reduce market beta while keeping the energy tilt","evidence_ids":["ev_hedge_001"]}],
  "confidence": "medium",
  "red_team": {"verdict":"proceed with caution","reasons":["Only 4 analogs match category ≥4 with a refinery track [ev_analogs_001]","Indian refiners also respond to the crude spread, not only US product prices","Sentiment confidence is low (FinBERT 0.52) [ev_sentiment_001]"]},
  "validator": {"numbers_found":14,"numbers_matched":14,"unmatched":[],"action":"pass"},
  "llm_usage": {"cloud_calls":0,"local_calls":8,"saved_calls":6}
}
```

### 4.2 Query B: Bay of Bengal cyclone with a monsoon deficit
> **"A severe cyclone is forming in the Bay of Bengal and the monsoon is 20% below normal in central India. How exposed are my FMCG and agri stocks?"**

**Expected intent:** `event_impact · cyclone · Bay of Bengal / Odisha · horizon 20 · tickers [ITC.NS, HINDUNILVR.NS, UPL.NS, DHANUKA.NS, COROMANDEL.NS]`

**Expected trace differences from A:** `agri_agent` **runs** (L2, agri model on cached NDVI for Odisha, Vidarbha and MP districts). `macro_agent` runs (CPI and food inflation). All 6 agents start in parallel. The synthesizer must mention that **crop stress is a slow signal** (from prompt P5).

**Expected `FinalAnswer` highlights:**
```json
{
  "bottom_line": "Kharif stress is likely in the exposed districts (stress_class 'stressed', p=0.60) [ev_agri_001], which historically weighs on agrochemical demand timing and rural FMCG volumes over weeks, not days [ev_analogs_002].",
  "holdings_impact": [
    {"ticker":"UPL.NS","impact":"negative","range":"-6.0% to +1.5% (20d)","evidence_ids":["ev_analogs_002","ev_agri_001"]},
    {"ticker":"DHANUKA.NS","impact":"negative","range":"-7.2% to +2.0%","evidence_ids":["ev_analogs_002"]},
    {"ticker":"ITC.NS","impact":"mixed","range":"-2.5% to +1.8%","evidence_ids":["ev_exposure_001"]},
    {"ticker":"HINDUNILVR.NS","impact":"mildly negative","range":"-3.1% to +1.2%","evidence_ids":["ev_analogs_002"]}
  ],
  "confidence": "low",
  "what_could_be_wrong": ["Yield labels are derived from VCI + rainfall thresholds, not measured yields [ev_agri_001]","The cyclone track is uncertain beyond 72 h [ev_weather_002]"]
}
```

`golden.yaml` encodes both queries, plus 8 variants from 05's prompt-eval set.

---

## 5. Chaos test matrix

Toggle these in the UI settings (`ChaosFlags`), or physically.

| # | Chaos | How | Expected behaviour | Pass when |
|---|---|---|---|---|
| C1 | `weather_down` | Settings toggle | Ingestion returns the cached weather with `degraded=true, degraded_reason="chaos"`, and confidence ×0.5 | Saffron node, DegradedBanner, answer says "weather data is stale" |
| C2 | `force_rate_limit` | Toggle, with `LLM_MODE=boost` | Gateway gets a 429 from Groq, falls through the chain to local `qwen3:4b-instruct` | `AgentEvent.provider` changes to `ollama@L1`, the run still finishes, and the quota meter shows "fallback" |
| C3 | `agri_raster_missing` | Toggle | The agri service returns its last cached signal with `degraded=true` and lower confidence | Answer mentions the degraded agri signal |
| C4 | `vector_down` | Toggle, or `docker stop weaviate` | `analog_agent` degraded. The distribution comes from the cached `events.json` brute-force cosine, or is skipped with `confidence:"low"` | The answer reports no single-number outcome |
| C5 | Kill L2 | Pull Wi-Fi on L2 | Every L2 tool times out (8 s) and falls back to cached or mock | The run finishes in under 30 s, and the health page shows L2 red |
| C6 | `slow_network_ms=3000` | Slider | Parallel fan-out still bounds latency (about the slowest agent, not the sum) | Total < sum of agent latencies |
| C7 | Hallucination probe | Unit test: inject "the stock will rise 37%" into the synthesizer output | The validator strips or flags 37% | `validator.action != "pass"` and the number appears in `unmatched` |
| C8 | Ollama on L1 down | `taskkill /IM ollama.exe` | Gateway tries L2 or L3 Ollama for the same role, then Groq | The run finishes and the provider shows the other host |

---

## 6. Cost table: can we run it many times an hour?

| Component | Cost | Limit that matters | Runs per hour possible |
|---|---|---|---|
| Ollama local models (qwen3:4b-instruct, gemma3:4b, phi4-mini, qwen3:1.7b) | ₹0 | GPU time only | Unlimited, roughly 6–15 s per run on 6 GB GPUs (measure it) |
| FinBERT, bge-small embeddings, LightGBM, quant engines | ₹0 | CPU/GPU | Unlimited |
| Weaviate (local Docker) | ₹0 | RAM | Unlimited |
| yfinance, Open-Meteo, RSS, NASA POWER | ₹0 | Soft rate limits | Unlimited with `CACHE_MODE=record`, then `replay` |
| Groq free tier (boost) | ₹0 | about 1,000 requests a day, plus tokens per minute and per day | About 2 cloud calls per run, so hundreds of runs a day. Use `auto` mode |
| Cerebras free tier (fallback) | ₹0 | 1M tokens a day, about 5 requests a minute | Backup only |
| OpenRouter Kimi free (optional) | ₹0 | 50 requests a day unfunded | Final-demo extra only |
| **Replay mode (`CACHE_MODE=replay`)** | ₹0 | none | **Unlimited, deterministic, offline** |

**Rule:** develop with `LLM_MODE=local`, regression-test with `CACHE_MODE=replay`, and use `boost` only for integration checks and the final demo.

---

## 7. Demo script (3 minutes)

| Time | Action | Talking point |
|---|---|---|
| 0:00 | The terminal is open, and the pet sits in the corner. Show `/health`: 7 services green across L1/L2/L3 | "Three laptops, seven services, four local LLMs, all free." |
| 0:20 | Type Query A | — |
| 0:25 | The live graph fans out to 6 agents in parallel across laptops | "The plan is deterministic. The LLMs only parse, narrate and synthesize." |
| 0:50 | The answer appears. Click `[ev_analogs_001]`, then the drawer shows 4 analogs with a p10/median/p90 range | "Every number traces to evidence. We report ranges, never a single number." |
| 1:10 | Red-team card plus Numbers Ledger 14/14 | "A second model argues against us, and code verifies every number." |
| 1:25 | What-if slider: crude +15%, and P&L updates | "The quant engine computes this. No LLM is involved." |
| 1:40 | Chaos: toggle `weather_down` and `force_rate_limit`, then re-run | Saffron degraded node; provider falls back from Groq to local qwen3 |
| 2:05 | A monitor alert fires (cyclone, Tier 2). The pet hops, the bubble shows, click **Analyze** to run Query B | "Agri stress from satellite NDVI, through our own model." |
| 2:35 | `/backtest`: scoreboard with honest misses, plus calibration plot | "We beat price-only on 5 of 8 events, and here are the 3 we got wrong." (Use your real numbers) |
| 2:50 | Disclaimer slide | "Decision support, not trading signals." |

## 8. Judge Q&A (prepare these answers)

- **Why local-first, not a cloud agent?** It costs nothing, has no rate limits, runs offline on stage, and is private. We run it hundreds of times a day. The cloud model is an optional boost with automatic fallback.
- **Why Groq rather than Kimi?** Free-tier capacity. Groq gives about 1,000 requests a day with OpenAI-compatible tool calling and very fast inference, against Kimi's 50 a day on an unfunded OpenRouter account. Our whole run needs about 2 cloud calls, and it falls back to local on any 429.
- **How do you prevent hallucinated numbers?** (1) Tools compute every number. (2) Prompts forbid any number that isn't in the EVIDENCE JSON. (3) The validator extracts every number from the answer and matches it to evidence, stripping or flagging unmatched ones. (4) The red team challenges the conclusion. (5) "Explain yourself" replays the stored state log.
- **Why a backtest?** It proves we beat simple baselines on events held out of the analog index, it shows our misses honestly, and it calibrates the stated confidence.
- **Is the agri model real ML?** Yes. It's a LightGBM classifier plus quantile regressors on zonal NDVI, VCI and rainfall features, validated with leave-one-year-out CV against a "predict normal" baseline. We state how the labels were made.
- **What if a laptop dies?** Every tool has a cached or mock fallback, and the health page shows it. We demonstrated this with chaos C5.
- **Isn't a 4B model too weak to plan?** That's why the plan is deterministic. The router picks agents from the intent, and the small model only fills arguments and writes text.

## 9. Backup video checklist
- [ ] Record both queries plus the chaos demo at 1080p with OBS, with captions or voice-over, on L1 in R2 mode
- [ ] Export the real runs with `GET /runs/{id}/events.jsonl`, then copy them to `apps/terminal/public/mock/` so the UI can replay them offline
- [ ] Copy the video to 2 USB drives and a phone. Keep the replay mode one click away (`NEXT_PUBLIC_MOCK=1`)
- [ ] Check the remaining Groq quota on the console before going on stage. If it's low, set `LLM_MODE=local`
- [ ] Pre-warm the models (`OLLAMA_KEEP_ALIVE=30m`, plus one warm-up query) 5 minutes before the demo
- [ ] Use a phone hotspot, with the venue Wi-Fi as the backup only, and run `check_health.py` at T-10 minutes

## 10. Cut order (if behind)
1. Telegram/email Tier 3
2. The pet's copilot-button flow (keep the alert bubble)
3. Angel One import (keep CSV)
4. The ML yield model in the agri harness (keep the NDVI anomaly and VCI)
5. Hindi/Hinglish answer toggle
6. Paper-trading P&L tracking (keep the approval list)

**Never cut:** vector DB analogs with ranges, the backtest, the Numbers Ledger validator, the live agent graph, or the chaos/degraded path.

## 11. Sources
- Groq rate limits: https://console.groq.com/docs/rate-limits
- Cerebras free tier: https://www.free-model.com/providers/cerebras/
- LangGraph parallel fan-out (`Send`): https://docs.langchain.com/oss/python/langgraph/use-graph-api
- OBS Studio: https://obsproject.com/
