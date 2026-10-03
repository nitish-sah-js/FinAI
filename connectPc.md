# Plan: 3-Laptop Multi-Model System (Weather + News → Kimi)

## Context
Hackathon project (NIT Raipur, empty folder). You want 3 laptops on the **same Wi-Fi/hotspot**, each running a different model, talking to each other:
- **Laptop 1 – Weather Agent**: pulls weather data, a small model turns it into a clean summary.
- **Laptop 2 – News Agent**: pulls news data, a small model turns it into a clean summary.
- **Laptop 3 – Kimi Orchestrator**: gets both summaries, Kimi reasons over them, shows the final answer to the user.

**Yes, it's possible.** The best-optimized approach for a hackathon: each laptop runs a small **FastAPI HTTP service** on the LAN. Laptop 3 calls Laptops 1 & 2 **in parallel**, then sends the combined result to Kimi. This is just REST calls over Wi-Fi — no special networking, easy to debug, fast. (Google's A2A protocol is an optional upgrade later; plain HTTP is quicker to get working.)

```
            User (browser)
                 │
        ┌────────▼─────────┐
        │ Laptop 3 :8000   │  Orchestrator + UI
        │ Kimi K3 (API)    │
        └───┬─────────┬────┘
   parallel │         │ parallel  (HTTP over same Wi-Fi)
   ┌────────▼───┐ ┌───▼────────┐
   │ Laptop 1   │ │ Laptop 2   │
   │ :8001      │ │ :8002      │
   │ Open-Meteo │ │ NewsAPI /  │
   │ + Ollama   │ │ GNews      │
   │ (qwen/     │ │ + Ollama / │
   │  llama)    │ │ Gemini API │
   └────────────┘ └────────────┘
```

## Tech choices (researched)
| Piece | Choice | Why |
|---|---|---|
| Weather data | **Open-Meteo** | Free, **no API key**, 10k calls/day |
| News data | **NewsAPI.org** (free dev key) or **GNews** (100 req/day free) | NewsAPI free tier is dev-only (fine for hackathon, called server-side); GNews as backup |
| Local models (L1, L2) | **Ollama** with `qwen3:4b` / `llama3.2:3b` (or a cloud API like Gemini if laptop is weak) | Runs on normal laptops, OpenAI-style API |
| Final model (L3) | **Kimi K3** via `https://api.moonshot.ai/v1`, model `kimi-k3` | OpenAI-compatible → use `openai` Python SDK |
| Services | **FastAPI + uvicorn + httpx** | Async, parallel calls, auto docs at `/docs` |
| UI | Simple HTML page served by Laptop 3 (or Streamlit) | Fast to build |

## Step-by-step

### Step 0 — All 3 laptops
1. Connect all to the **same Wi-Fi or one phone hotspot** (hotspot is safest — college Wi-Fi often blocks device-to-device traffic; if so, install **Tailscale** on all 3 and use their `100.x.x.x` IPs instead).
2. Set the network to **Private** in Windows (Settings → Network → Wi-Fi → Private network).
3. Install Python 3.11+, then: `pip install fastapi uvicorn httpx openai python-dotenv`
4. Find each laptop's IP: `ipconfig` → "IPv4 Address" (e.g. `192.168.43.101`). Write them down.
5. Open firewall port (PowerShell **as Admin**):
   `New-NetFirewallRule -DisplayName "Agent" -Direction Inbound -LocalPort 8000-8002 -Protocol TCP -Action Allow`
6. Test connectivity: from Laptop 3, `ping 192.168.43.101`.

### Step 1 — Laptop 1: Weather Agent (`weather_agent/main.py`, port 8001)
- Install Ollama → `ollama pull qwen3:4b`
- `POST /weather {city}` → geocode city via Open-Meteo geocoding API → fetch current + forecast → ask local Ollama model (`http://localhost:11434/api/chat`) to summarize in ≤5 lines → return `{"city":..., "raw":..., "summary":...}`.
- Run: `uvicorn main:app --host 0.0.0.0 --port 8001`  (**`0.0.0.0` is required** so other laptops can reach it)

### Step 2 — Laptop 2: News Agent (`news_agent/main.py`, port 8002)
- Install Ollama → `ollama pull llama3.2:3b` (different model from Laptop 1)
- `.env`: `NEWS_API_KEY=...`
- `POST /news {topic/city}` → NewsAPI `/v2/everything?q=...` (top 5 articles) → local model summarizes headlines → return `{"articles":[...], "summary":...}`.
- Run: `uvicorn main:app --host 0.0.0.0 --port 8002`

### Step 3 — Laptop 3: Kimi Orchestrator (`orchestrator/main.py`, port 8000)
- `.env`: `MOONSHOT_API_KEY=...`, `WEATHER_URL=http://<L1-IP>:8001`, `NEWS_URL=http://<L2-IP>:8002`
- `POST /ask {query, city}`:
  1. `asyncio.gather()` → call `/weather` and `/news` **in parallel** with `httpx.AsyncClient(timeout=60)`.
  2. If one agent fails, continue with the other (graceful fallback).
  3. Send both summaries to Kimi:
     ```python
     from openai import OpenAI
     kimi = OpenAI(api_key=os.getenv("MOONSHOT_API_KEY"), base_url="https://api.moonshot.ai/v1")
     resp = kimi.chat.completions.create(model="kimi-k3", messages=[
       {"role":"system","content":"You combine weather and news into a helpful final answer."},
       {"role":"user","content":f"Question: {q}\nWeather: {w}\nNews: {n}"}])
     ```
  4. Return final answer + which agents responded.
- `GET /` → simple HTML page with a text box (user types question + city).
- Run: `uvicorn main:app --host 0.0.0.0 --port 8000` → open `http://localhost:8000`.

### Step 4 — Optimizations
- **Parallel calls** (gather) — total latency ≈ slowest agent, not the sum.
- Agents send **short summaries**, not raw JSON → fewer Kimi tokens, faster, cheaper.
- `/health` endpoint on each agent; Laptop 3 shows green/red status per laptop (looks great in a demo).
- Cache weather/news for 5–10 min (simple dict with timestamp) to avoid API rate limits.
- Ollama: set `OLLAMA_KEEP_ALIVE=30m` so the model stays loaded (no cold-start delay in demo).
- Keep a **fallback**: if a laptop dies, the orchestrator still answers with what it has.

### Optional upgrade
Wrap each agent with **A2A protocol** (`pip install a2a-sdk`) — agent cards + standard messaging; good "wow" factor for judges but not needed for it to work.

## Files to create (in `NIT_Raipur/`)
- `weather_agent/main.py`, `weather_agent/requirements.txt`
- `news_agent/main.py`, `news_agent/.env.example`, `requirements.txt`
- `orchestrator/main.py`, `orchestrator/static/index.html`, `orchestrator/.env.example`
- `README.md` with run instructions per laptop

## Verification
1. On each agent laptop: open `http://localhost:8001/docs` (or 8002) and test the endpoint.
2. From Laptop 3 browser: `http://<L1-IP>:8001/health` and `http://<L2-IP>:8002/health` → must return OK (if not: firewall / `0.0.0.0` / Private network).
3. On Laptop 3: open `http://localhost:8000`, ask "Should I go out in Raipur today?" → final Kimi answer uses both weather + news.
4. Kill Laptop 2's server → ask again → still get a weather-only answer (fallback works).

## Sources
- [Ollama LAN access on Windows](https://www.digitalcitizen.life/how-to-expose-ollama-to-the-network-on-windows/), [Windows firewall fix](https://llmconfigurator.com/en/guides/troubleshooting/ollama-not-reachable-lan-windows-firewall)
- [Kimi API OpenAI compatibility](https://platform.kimi.ai/docs/guide/migrating-from-openai-to-kimi), [Kimi K3 API guide](https://www.verdent.ai/guides/agents/kimi-k3-api-guide)
- [Open-Meteo](https://open-meteo.com/), [Free weather APIs 2026](https://freeapi.watch/blog/best-free-weather-apis-2026/)
- [NewsAPI alternatives 2026](https://newsmesh.co/blog/newsapi-alternatives-2026)
- [Tailscale for client-isolated Wi-Fi](https://logarithmicspirals.com/blog/using-tailscale-to-access-private-llms/)
- [A2A protocol](https://github.com/a2aproject/A2A), [Multiple local LLMs 2026](https://www.sitepoint.com/multiple-local-llms-setup-2026/)
