# Orchestrator: LangGraph + FastAPI on L1:8000 (docs/04)

## Setup (once, from the repo root, PowerShell)
```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env          # MOCK=1 by default: runs with nothing else installed
```

## Run
```powershell
cd services
..\.venv\Scripts\python -m uvicorn orchestrator.app:app --host 0.0.0.0 --port 8000
# or a single query in the terminal, with live events:
..\.venv\Scripts\python -m orchestrator.cli "Cyclone heading to Odisha — what happens to my portfolio this week?"
..\.venv\Scripts\python -m orchestrator.cli --chaos weather_down,force_rate_limit --mode boost "..."
```
- **MOCK=1:** fixtures plus canned LLM output. A full run takes under 1 s with about 30 events. Use this to build the frontend (13).
- **MOCK=0 with services missing:** each missing tool falls back to its fixture marked `degraded: service_unreachable`. If Ollama is down, the keyword intent parser, code narrators, template answer and rule-based red team take over. Down hosts are skipped for 30 s, so the run still finishes in seconds.
- **Real local LLMs:** run `infra\ollama_setup.ps1 -Laptop L1` (pulls `qwen3:4b-instruct`; do not use the thinking-only `qwen3:4b`). The server and CLI warm the model up on start. Check with `GET /llm/health`.
- **Groq boost:** set `GROQ_API_KEY` and `LLM_MODE=auto`. The synthesizer then uses Groq while daily quota remains above `AUTO_MIN_RPD`; everything else stays local.

## API (04 §8)
`POST /query` · `WS /ws/{run_id}` · `WS /ws/activity` · `GET /runs` · `GET /runs/{id}` · `GET /runs/{id}/events.jsonl` ·
`GET /runs/{id}/explain` · `POST /runs/{id}/resume` · `GET /llm/quota` · `GET /llm/health` · `GET /health` · `GET /health/all` ·
`GET/POST /portfolio` · `GET /portfolio/{id}` · `POST /portfolio/upload` (CSV) · `POST /tools/scenario` · `GET /backtest/scoreboard`.
`/paper/*` (doc 12) is mounted automatically once `services/orchestrator/paper/router.py` exists.

## Layout
| File | What |
|---|---|
| `graph.py` | StateGraph with `Send` fan-out, `run_graph()` (importable by the backtest), `resume_run()`, `build_final()` |
| `router.py` | deterministic intent → agents table, quant plan, what-if shock parser |
| `nodes/` | parse_intent (P1 + bounded schema + keyword fallback), 6 agents, join, planner (P2), quant_agent, synthesizer (P8/P8_hi with pre-written cited lines + template), red_team (P9 + `clean_reasons` + rules), validator (Numbers Ledger with auto-citation), explain (P10) |
| `tools_client.py` | HTTP to L2/L3 with chaos, mock, unreachable→fixture, host-down memory, time-machine guard, per-run evidence renumbering |
| `events.py` / `ledger.py` | WebSocket bus (late-subscriber replay) and SQLite `data/ledger.db`. Checkpoints go to `data/checkpoints.db` |
| `staleness.py`, `budget.py`, `regions.py`, `portfolio.py`, `prompt_loader.py`, `prompts/` | as in 04 §3–4 and 05 (prompt files tuned with `evals/`) |

## Test
```powershell
cd services
..\.venv\Scripts\python -m pytest orchestrator/tests -q        # 44 tests
```
