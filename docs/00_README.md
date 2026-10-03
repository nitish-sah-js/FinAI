# Copilot: Indian-Market Intelligence Terminal (module index)

A **local-first, multi-agent decision-support terminal** for Indian markets. It links weather, ISRO crop imagery, news sentiment, macro data and historical analogs to *your* portfolio. It then runs real quant engines (event study, VaR and Monte Carlo, hedging) and writes a cited answer in which every number traces to an evidence ID.

> Decision support only. These are not trading signals. No real orders are placed.

**Everything runs for free and without limits.** Small local models on Ollama do most of the work. Groq's free tier is an optional "boost" for the final synthesis. Every external call is cached, so you can run the full pipeline as often as you like.

---

## How to use these docs
1. **Everyone reads [01_CONTRACTS.md](01_CONTRACTS.md) first.** It defines the shared models, ports, names and conventions.
2. Pick your module file. Each one has the same sections:
   **Header → Folder layout → Step-by-step build prompt (paste into any coding LLM: Kimi Adagio chat, Claude, ChatGPT, Copilot) → Example outputs → Mock mode → Acceptance checklist → Integration hooks → Sources.**
3. Build with `MOCK=1` first, so you never wait on another person. Swap to real calls once the other modules are ready.
4. When pasting a build prompt into an LLM, **also paste sections 4–7 of 01_CONTRACTS.md** so it uses the exact shapes.

## Module files

| # | File | What it builds | Laptop | Suggested owner |
|---|---|---|---|---|
| 01 | [01_CONTRACTS.md](01_CONTRACTS.md) | Shared Pydantic models, IDs, cache, service base, fixtures | all | Lead (P1) |
| 02 | [02_NETWORK_INFRA.md](02_NETWORK_INFRA.md) | LAN, firewall, Ollama setup, Weaviate docker, health script | all | P1 |
| 03 | [03_LLM_GATEWAY.md](03_LLM_GATEWAY.md) | One LLM client: local Ollama ⇄ Groq/Cerebras/OpenRouter fallback, cache, quota | L1 | P1 |
| 04 | [04_ORCHESTRATOR_LANGGRAPH.md](04_ORCHESTRATOR_LANGGRAPH.md) | LangGraph parallel graph, WebSocket, ledger, validator, explain | L1 | P1 |
| 05 | [05_RUNTIME_PROMPTS.md](05_RUNTIME_PROMPTS.md) | P1–P11 runtime prompts and the prompt-eval harness | L1 | P1 / P5 |
| 06 | [06_QUANT_ENGINE.md](06_QUANT_ENGINE.md) | Event study, correlations, VaR/MC, scenario, hedges, exposure | L2 | P2 |
| 07 | [07_SENTIMENT_SERVICE.md](07_SENTIMENT_SERVICE.md) | FinBERT-India and gemma3 second opinion | L2 | P3 |
| 08 | [08_VECTOR_DB.md](08_VECTOR_DB.md) | Weaviate analogs, event seeding, news index | L2 | P3 |
| 09 | [09_ISRO_AGRI.md](09_ISRO_AGRI.md) | NDVI pipeline, crop-stress model, `/agri_signal` | L2 | P2 / P3 |
| 10 | [10_DATA_INGESTION.md](10_DATA_INGESTION.md) | Prices, news, weather, macro, announcements, plus cache and replay | L3 | P4 |
| 11 | [11_MONITOR_ALERTS.md](11_MONITOR_ALERTS.md) | Anomaly detectors and tiered alerts | L3 | P4 |
| 12 | [12_BACKTEST_PAPER_TRADING.md](12_BACKTEST_PAPER_TRADING.md) | Backtest scoreboard, calibration, paper trades | L1/L2 | P2 |
| 13 | [13_FRONTEND_TERMINAL.md](13_FRONTEND_TERMINAL.md) | Next.js terminal, live agent graph, dashboards | L3 | P5 |
| 14 | [14_DESKTOP_PET.md](14_DESKTOP_PET.md) | Electron always-on-top pet | L3 | P5 |
| 15 | [15_INTEGRATION_AND_DEMO.md](15_INTEGRATION_AND_DEMO.md) | Integration order, E2E tests, chaos matrix, demo script | all | Lead |
| 16 | [16_IMPROVEMENTS.md](16_IMPROVEMENTS.md) | Improvements (where each is built) and stretch backlog | — | — |

> `connectPc.md` (in the project root) is the earlier weather/news/Kimi plan. **It is superseded by these docs.** Its networking steps live on in 02.

### With 3 people instead of 5
- **P1 (L1):** 01, 02, 03, 04, 05, 15
- **P2 (L2):** 06, 07, 08, 09, 12
- **P3 (L3):** 10, 11, 13, 14

---

## Architecture

```
                 ┌───────────── L3 "Edge & UI" ─────────────┐
 User ─────────► │ Next.js terminal :3000   Electron pet     │
                 │ Ingestion :8201          Monitor :8202    │◄── RSS, GDELT, yfinance, Open-Meteo,
                 │ Ollama qwen3:1.7b, phi4-mini              │    NASA POWER, IMD, RBI, FRED, EIA, NSE
                 └──────────────┬───────────────────────────┘
                     POST /query │ ▲ WS /ws/{run_id} (AgentEvent stream)
                 ┌──────────────▼───────────── L1 "Brain" ───┐
                 │ Orchestrator :8000 (FastAPI + LangGraph)    │
                 │ parse_intent → router ─┬─ sentiment_agent   │      optional boost:
                 │                        ├─ weather_agent     │ ───► Groq gpt-oss-120b / qwen3-32b
                 │      (Send fan-out,    ├─ agri_agent        │      → Cerebras → OpenRouter Kimi
                 │       parallel)        ├─ macro_agent       │      (fallback: local qwen3:4b-instruct)
                 │                        ├─ analog_agent      │
                 │                        └─ exposure_agent    │
                 │ → join → quant_agent → synthesizer          │
                 │ → red_team → validator → FinalAnswer        │
                 │ Ollama qwen3:4b-instruct · SQLite ledger/checkpoints │
                 └──────────────┬─────────────────────────────┘
                                │ HTTP (X-Run-Id)
                 ┌──────────────▼──────────── L2 "Quant & ML" ┐
                 │ Quant :8101  Sentiment :8102 (FinBERT)      │
                 │ Agri :8103 (NDVI GBM)  Vector :8104         │
                 │ Weaviate :8080 · Ollama gemma3:4b           │
                 └─────────────────────────────────────────────┘
```

### Which model does what

| Job | Model | Runs on | Why |
|---|---|---|---|
| Intent parse (P1), arg filler (P2), explain (P10) | `qwen3:4b-instruct` (think off) | L1 Ollama | Best small tool-caller |
| Narrators: weather, agri, macro, analogs (P4–P7); sentiment second opinion (P3) | `gemma3:4b` | L2 Ollama | Different model family gives a genuine second opinion |
| Red team (P9) | `phi4-mini` | L3 Ollama | Strong reasoning for its size |
| Alert one-liners (P11) | `qwen3:1.7b` | L3 Ollama | Very fast |
| Synthesizer (P8) | Groq `openai/gpt-oss-120b` (boost) → local `qwen3:4b-instruct` | cloud / L1 | Best answer quality where it counts |
| Headline sentiment | `kdave/FineTuned_Finbert` | L2 | India-tuned FinBERT |
| Embeddings | `BAAI/bge-small-en-v1.5` (384-d) | L2 CPU | Small and good |
| Crop stress / yield | LightGBM (your trained model) | L2 | Real ML on ISRO/Sentinel NDVI |
| Numbers, risk, hedges | numpy/scipy code | L2 | The LLM never does the maths |

---

## Module dependency graph

```
01 contracts ──► everything
02 infra ──────► 03, 04, 08 (Weaviate), all services
03 llm ────────► 04, 07 (2nd opinion), 11 (alert writer)
05 prompts ────► 04, 07, 11
10 ingestion ──► 06 (prices), 07 (news), 08 (news index), 11, 12
06, 07, 08, 09 ► 04 (as tools)      08 holdout list ► 12
04 orchestrator► 13, 14, 12 (time-machine runs)
11 monitor ────► 13, 14
```
With `MOCK=1` fixtures, **no arrow blocks anyone during H1–H8.**

---

## Timeline (24 h)

| Hours | Milestone | Done when |
|---|---|---|
| H0–1 | 01 contracts merged. 02: all laptops ping each other and Ollama is reachable over LAN. Each person tests their model or API (Qwen3 tool call, Groq key, FinBERT load, Bhoonidhi/Sentinel access) | `check_health.py` shows every host |
| H1–8 | Every service runs in `MOCK=1`, then with real logic and cache. Orchestrator runs on fixtures. UI renders a recorded event stream | Each module's acceptance checklist is ≥70% ticked |
| H8–14 | Real tools behind the orchestrator. **Hurricane and cyclone queries run end to end at H14** | 15 §E2E-1 passes |
| H14–20 | Analog ranges, red team, validator, backtest, alerts, pet, paper trading, chaos toggle | 15 §E2E-2 and the chaos matrix pass |
| H20–22 | Harden, add the disclaimer, **freeze**, switch to `CACHE_MODE=replay` for the demo | Demo works offline |
| H22–24 | Backup video, 5 rehearsals, judge Q&A | 15 §Demo script |

**Cut order if you fall behind:** Telegram → the pet's popup flow → Angel One import → yield regressor (keep the NDVI anomaly and stress class) → Hindi toggle → what-if sliders.
**Never cut:** vector-DB analogs, the backtest, the Numbers Ledger validator, the live agent graph.

---

## Free-to-run guarantee

| Resource | Cost | Limit you'll hit? |
|---|---|---|
| Ollama models | Free, local | None (GPU time only) |
| Groq free tier | Free | ~1,000 requests/day/model plus a TPM cap. We use ≤2 per query, and replays cost 0 |
| Cerebras free tier | Free | ~1M tokens/day. Backup only |
| Open-Meteo, NASA POWER, GDELT, RSS, yfinance | Free, no key (FRED/EIA need a free key) | Cached, so roughly 1 call per key per TTL |
| Weaviate | Free, local Docker | None |

**Development mode:** `LLM_MODE=local CACHE_MODE=record` gives unlimited runs.
**Demo mode:** `LLM_MODE=auto CACHE_MODE=replay` gives instant, reproducible runs that need no network.
