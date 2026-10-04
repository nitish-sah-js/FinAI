# Sigma: portfolio risk copilot for Indian markets

You ask a question, such as "A cyclone hits Odisha during a weak monsoon. What happens to my FMCG holdings?". A
LangGraph orchestrator then sends the work to seven services: news and sentiment, weather, an ISRO/MODIS crop model,
macro data, past-event analogs in Weaviate, a quant engine for exposure, VaR, scenarios and hedges, and a monitor for
alerts. A local LLM writes the answer, and a Numbers Ledger checks every figure against the evidence. The desktop app
(Next.js + Electron) shows the answer, the agent graph, the latency timeline, paper trades, the backtest and the cluster.

Everything runs locally on Ollama. Nothing reaches a broker: hedges are only paper trades, and they need a human to
approve them.

## Run on one laptop

```powershell
python -m venv .venv; .venv\Scripts\pip install -r requirements.txt -r services\quant\requirements.txt `
  -r services\sentiment\requirements.txt -r services\agri\requirements.txt -r services\vectordb\requirements.txt `
  -r services\ingestion\requirements.txt -r services\monitor\requirements.txt
copy .env.example .env                                     # then set MOCK=0 for real data
ollama pull qwen3:4b-instruct
docker compose -f infra\docker-compose.yml up -d           # Weaviate (vectordb seeds it on start)
powershell -ExecutionPolicy Bypass -File infra\run_all_local.ps1          # services + desktop app
powershell -ExecutionPolicy Bypass -File infra\run_all_local.ps1 -Stop    # stop everything
```

`MOCK=1` runs on canned fixtures with no Ollama and no internet, which is useful for UI work. With `MOCK=0`, missing
data is reported as unavailable and never replaced with fixtures.

## Run on three laptops

| Laptop | Runs | Ollama model |
|---|---|---|
| L1 Brain | orchestrator :8000 | `qwen3:4b-instruct` (intent, synthesizer, explain) |
| L2 Compute | quant :8101, sentiment :8102, agri :8103, vectordb :8104, Weaviate :8080 (Docker) | `gemma3:4b` (narrators, sentiment second opinion) |
| L3 Edge | ingestion :8201, monitor :8202, desktop app :3000 | `qwen3:1.7b` (alert text), `phi4-mini` (red team) |

Every service checks a shared key (`X-Cluster-Key`). `/health` stays open.

**Setup, on L1:**
1. Put the three laptops on one phone hotspot or travel router. Set the network profile to **Private** on each.
2. `copy deploy\cluster.env.example deploy\cluster.env`, then enter the three IPs from `ipconfig`.
3. `powershell -ExecutionPolicy Bypass -File deploy\gen_cluster.ps1`. This writes a `.env` for each laptop and
   generates the key.
4. `copy deploy\out\L1.env .env`
5. `powershell -ExecutionPolicy Bypass -File deploy\make_bundles.ps1`. This builds `deploy\out\L2_bundle.zip` and
   `L3_bundle.zip`. They contain the key and SMTP secrets, so move them by USB and never post them.
6. On L2 and L3: unzip the bundle. Run `infra\firewall.ps1` in an Admin PowerShell, then `setup_Lx.ps1 -PullModels`.

**Run order: L2, then L3, then L1.**
- L2: `.\start_L2.ps1` (Weaviate + 4 services)
- L3: `.\start_L3.ps1` (ingestion, monitor, desktop app)
- L1: `powershell -ExecutionPolicy Bypass -File infra\run_all_local.ps1 -Laptop L1`

Then, on L1, run `powershell -ExecutionPolicy Bypass -File deploy\verify_cluster.ps1`. It pings every service and
each laptop's Ollama, and checks the key: 401 without it, accepted with it. It exits 1 on any failure. The app's
**Cluster** page shows the same live, along with GPU memory, loaded models and news-indexing delay.

More detail is in [deploy/README.md](deploy/README.md) (steps) and [infra/README.md](infra/README.md) (network, Ollama,
Weaviate, measured speeds).

## Check that everything is really used

```powershell
.venv\Scripts\python scripts\verify_usage.py        # 8 fixed questions; exits 1 and lists what was not used
.venv\Scripts\python -m backtest.run_backtest --split holdout --mode local --cache record
.venv\Scripts\python evals\run_evals.py
```

Every run writes a trace (`GET /runs/{run_id}/trace`). It records which node called which service or model, on which
laptop, how long it took, and whether the call was `ok`, `degraded`, `fallback` (another model answered), `cached` or
`unavailable`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `verify_cluster`: `unreachable (ConnectTimeout)` for one laptop | **Firewall or network profile.** On that laptop, run `infra\firewall.ps1 -Check`. The profile must be Private. Run `infra\firewall.ps1` as Admin |
| Every laptop times out on every other laptop, but the hotspot works | **AP / client isolation.** College and lab Wi-Fi often block traffic between devices. Use a phone hotspot or travel router. Test with `ping <ip>` (allowed by `firewall.ps1`) |
| `ollama ... unreachable` from L1, but `curl localhost:11434` works on that laptop | **Ollama host binding.** Ollama listens on 127.0.0.1 by default. `infra\ollama_setup.ps1 -Laptop Lx` sets `OLLAMA_HOST=0.0.0.0:11434`. Restart Ollama afterwards, from the tray app or with `ollama serve` |
| `missing gemma3:4b` / `phi4-mini` / `qwen3:1.7b` | Run `ollama pull <tag>` on that laptop. Until then, the role falls back to L1's model and the trace marks it `fallback` |
| vectordb `weaviate: down`, analogs `numpy fallback` (degraded) | **Docker.** Start Docker Desktop on L2, then `docker compose -f infra\docker-compose.yml up -d`. vectordb seeds the 40 events itself (`weaviate_events: ok` in `/health`) |
| `KEY AUTH FAIL` or 401 everywhere | That laptop's `.env` has an old `CLUSTER_KEY`. Re-run `gen_cluster`, then `make_bundles`, copy the bundle again and restart |
| The orchestrator answers with another app's page | Another program holds :8000 (seen with a Docker container). Run `docker ps`, then stop it. Use `127.0.0.1`, not `localhost` |
| First question takes about 40 s | The model is loading. `run_all_local.ps1` warms it up first; the app shows "warming up" until it is ready |

## Repository

| Path | What |
|---|---|
| `services/orchestrator` | LangGraph graph, router, trace, validator, conversation fast path, paper trading |
| `services/{quant,sentiment,agri,vectordb,ingestion,monitor}` | The six tool services (FastAPI, `/health`, `/llm/calls`) |
| `packages/copilot_common`, `packages/copilot_llm` | Shared contracts, settings, cluster auth, LLM gateway (roles, fallback, cache, call log) |
| `apps/terminal` | Next.js + Electron desktop app |
| `backtest/`, `evals/`, `scripts/verify_usage.py` | Honest measurement: held-out backtest, prompt evals, usage proof |
| `deploy/`, `infra/` | 3-laptop tooling, network, Ollama, Weaviate |
| `docs/` | Design docs 00–16. `docs/REPORT.md` is the latest delivery report |
