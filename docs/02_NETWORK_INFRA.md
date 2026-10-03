# 02 — Network & Infrastructure (3 laptops over Wi-Fi)

| | |
|---|---|
| **Owner / Laptop** | Whoever owns L1 "Brain" (sets up all 3 laptops with a helper from each) |
| **Depends on** | [01_CONTRACTS.md](01_CONTRACTS.md) §1 (port map), §3 (env vars) |
| **Provides** | A working LAN, Ollama reachable on every laptop, Weaviate running on L2, `infra/env/L*.env`, `infra/docker-compose.yml`, `infra/check_health.py`, `infra/firewall.ps1` |
| **Build when** | H0–H1, at the same time as 01 |

> `connectPc.md` (the earlier weather/news/Kimi plan) is **superseded** by this file. Keep it for reference only.

---

## 1. Folder layout

```
infra/
├── docker-compose.yml        # Weaviate on L2
├── firewall.ps1              # run as Admin on every laptop
├── ollama_setup.ps1          # env vars + model pulls, per laptop
├── check_health.py           # pings every /health + Ollama + Weaviate, prints a table
├── lan_test.ps1              # 1 s TCP probe of every port on the 3 laptops
├── make_env.py, use_env.ps1  # generate env files with real IPs / activate one as .env
├── tests/                    # python -m pytest infra/tests
├── env/
│   ├── L1.env  L2.env  L3.env
│   └── single.env            # everything on 127.0.0.1 (demo fallback)
└── README.md
```

---

## 2. Network choice (in order of preference)

| Option | When | Notes |
|---|---|---|
| **Phone hotspot** (one phone, 3 laptops) | Default | Hotspots almost never isolate clients. Keep the phone charging. IPs are usually `192.168.43.x` (Android) or `172.20.10.x` (iPhone) |
| **Travel router** (TP-Link etc.) | Best, if someone owns one | Lets you set DHCP reservations, so IPs stay fixed |
| **Venue / college Wi-Fi** | Only if laptop-to-laptop ping works | Many venues enable *client isolation*, which blocks LAN traffic |
| **Tailscale** (free) | Fallback for any network that isolates clients | Install on all 3 and use the `100.x.y.z` IPs in `.env`. It works over any internet connection, including different networks |

**Test laptop-to-laptop connectivity in the first hour.** Do not assume it works.

### Windows: set the network to Private
Go to Settings → Network & internet → Wi-Fi → (your network) → **Network profile type: Private**.
Public profile blocks inbound connections, even with firewall rules.

### Find IPs and pin them
```powershell
ipconfig | findstr IPv4
```
Write them into `infra/env/L1.env`, `L2.env` and `L3.env` (`L1_HOST`, `L2_HOST`, `L3_HOST`). If the hotspot reassigns IPs, change only these three lines. Every URL is derived from them (see 01 §3).

---

## 3. Firewall (`infra/firewall.ps1`, run in an **Admin** PowerShell on every laptop)

```powershell
# Copilot hackathon ports — inbound TCP on Private profile only
$ports = "8000,8101-8104,8201-8202,3000,8080,50051,11434"
New-NetFirewallRule -DisplayName "Copilot-Hackathon" -Direction Inbound `
  -Protocol TCP -LocalPort $ports -Action Allow -Profile Private
# allow ping for diagnostics
New-NetFirewallRule -DisplayName "Copilot-ICMP" -Protocol ICMPv4 -IcmpType 8 `
  -Direction Inbound -Action Allow -Profile Private
Get-NetFirewallRule -DisplayName "Copilot-*" | Format-Table DisplayName, Enabled, Profile
```
To remove the rules after the hackathon: `Remove-NetFirewallRule -DisplayName "Copilot-*"`.

Every service must bind to **`0.0.0.0`** (`uvicorn app:app --host 0.0.0.0 --port 8101`), not `127.0.0.1`. Otherwise other laptops can't reach it.

---

## 4. Ollama on every laptop (`infra/ollama_setup.ps1`)

```powershell
# persistent user env vars (restart Ollama from the tray afterwards)
setx OLLAMA_HOST "0.0.0.0:11434"     # listen on LAN
setx OLLAMA_KEEP_ALIVE "30m"         # keep models loaded (no cold start in demo)
setx OLLAMA_CONTEXT_LENGTH "8192"    # context size (the OpenAI endpoint can't set num_ctx per request)
setx OLLAMA_NUM_PARALLEL "2"         # 2 concurrent requests per loaded model
setx OLLAMA_MAX_LOADED_MODELS "2"    # L1/L3 hold 2 models
setx OLLAMA_FLASH_ATTENTION "1"      # less VRAM for KV cache
setx OLLAMA_KV_CACHE_TYPE "q8_0"     # halves KV cache memory, small quality cost
```
Quit Ollama from the system tray and start it again so it picks up the variables. Check from another laptop:
`curl http://<IP>:11434/api/tags`

### Models per laptop (6 GB VRAM each)

| Laptop | Pull | Q4 size (approx.) | Role |
|---|---|---|---|
| L1 | `ollama pull qwen3:4b-instruct` | ~2.5 GB | intent, synthesizer, explain, local fallback for everything |
| L2 | `ollama pull gemma3:4b` | ~3.3 GB | narrators, sentiment second opinion, Hindi output |
| L3 | `ollama pull qwen3:1.7b`, `ollama pull phi4-mini` | ~1.4 + ~2.5 GB | alerts, red team |
| All (backup) | `ollama pull qwen3:4b-instruct` on L2 and L3 too | | lets any laptop take over any role |

Verify the tags with `ollama list` and the [Ollama library](https://ollama.com/library). Run `ollama run qwen3:4b-instruct "hi" --verbose` once and **record tokens per second** for each laptop in `infra/README.md`.

### VRAM budget (6 GB)

| Laptop | Item | VRAM |
|---|---|---|
| L1 | qwen3:4b-instruct weights + 8K ctx KV (q8) ×2 parallel | ~3.5 GB, leaving headroom |
| L2 | gemma3:4b (~4 GB with ctx) + FinBERT fp16 (~0.25 GB) + bge-small (~0.1 GB) | ~4.5–5 GB. If you see OOM, move embeddings to CPU (`EMBED_DEVICE=cpu`) and FinBERT to CPU |
| L3 | qwen3:1.7b (~2 GB) + phi4-mini (~3 GB) | ~5 GB. Next.js and Electron use the CPU |

Context is 8192, set server-side with `OLLAMA_CONTEXT_LENGTH` (verified: Ollama's OpenAI endpoint ignores a per-request `num_ctx`). Do not raise it, because the KV cache grows linearly with context.
Measured on L1 (RTX 4050 6 GB): `qwen3:4b-instruct` 62.7 tok/s generate, 3.6 GB VRAM with q8_0 KV and 2 parallel slots.

---

## 5. Weaviate on L2 (`infra/docker-compose.yml`)

```yaml
services:
  weaviate:
    image: cr.weaviate.io/semitechnologies/weaviate:1.39.8   # latest stable on 2026-10-01; re-check on day 0
    command: ["--host", "0.0.0.0", "--port", "8080", "--scheme", "http"]
    ports:
      - "8080:8080"
      - "50051:50051"
    restart: unless-stopped
    volumes:
      - weaviate_data:/var/lib/weaviate
    environment:
      QUERY_DEFAULTS_LIMIT: 25
      AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED: "true"
      PERSISTENCE_DATA_PATH: "/var/lib/weaviate"
      DEFAULT_VECTORIZER_MODULE: "none"     # we bring our own bge-small vectors
      ENABLE_MODULES: ""
      CLUSTER_HOSTNAME: "node1"
volumes:
  weaviate_data:
```
Run `docker compose -f infra/docker-compose.yml up -d`, then check with `curl http://localhost:8080/v1/.well-known/ready`.
Docker Desktop on Windows needs WSL2. Install it before the hackathon. Give Docker at least 2 GB of RAM.

---

## 6. Health checker (`infra/check_health.py`)

Prints a green and red table of every service, Ollama and Weaviate. Run it from any laptop.

Expected output:
```
SERVICE        HOST            STATUS    LAT   MOCK  MODELS/DEPS
orchestrator   192.168.43.101  ok        12ms  no    qwen3:4b-instruct
quant          192.168.43.102  ok         9ms  no
sentiment      192.168.43.102  ok        15ms  no    kdave/FineTuned_Finbert(cuda)
agri           192.168.43.102  degraded  11ms  no    gbm_v1; rasters:6/8
vectordb       192.168.43.102  ok        20ms  no    weaviate:ok bge-small(cpu)
ingestion      192.168.43.103  ok         8ms  no
monitor        192.168.43.103  ok         7ms  no
ollama@L1      192.168.43.101  ok        30ms  -     qwen3:4b-instruct
ollama@L2      192.168.43.102  ok        28ms  -     gemma3:4b
ollama@L3      192.168.43.103  ok        31ms  -     qwen3:1.7b, phi4-mini
weaviate       192.168.43.102  ok         5ms  -
```

---

## 7. Build prompt (paste into a coding LLM)

```
You are setting up infrastructure for a 3-laptop Windows hackathon project. Produce these files:

1. infra/env/L1.env, L2.env, L3.env, single.env — identical keys (copy the env block from
   01_CONTRACTS.md §3). single.env sets L1_HOST=L2_HOST=L3_HOST=127.0.0.1.
2. infra/firewall.ps1 — exactly the script in 02_NETWORK_INFRA.md §3, plus a check that it runs
   as Administrator (exit with a message otherwise) and a check that the active network profile is
   Private (Get-NetConnectionProfile); if Public, print how to change it.
3. infra/ollama_setup.ps1 — takes -Laptop L1|L2|L3, sets the setx variables from §4, then runs
   `ollama pull` for that laptop's models (table §4), then `ollama list`.
4. infra/docker-compose.yml — Weaviate as in §5.
5. infra/check_health.py — Python 3.11, httpx async, rich (optional). Load .env via
   copilot_common.settings.Settings. Concurrently GET:
     {ORCH_URL,QUANT_URL,SENTIMENT_URL,AGRI_URL,VECTOR_URL,INGEST_URL,MONITOR_URL}/health  (Health model)
     {OLLAMA_L1,L2,L3}/api/tags  (list model names)
     http://{WEAVIATE_HOST}:8080/v1/.well-known/ready
   timeout 2 s each. Print a table like §6 (green ok / yellow degraded / red down).
   Flags: --watch (refresh every 5 s), --json (machine output for the frontend health page).
   Exit code 1 if any required service is down.
6. infra/lan_test.ps1 — given 3 IPs, Test-NetConnection each IP on ports 8000, 8101, 8201, 11434
   and print which ones fail, with the 3 most likely causes (Public profile, firewall, bound to 127.0.0.1).
7. infra/README.md — step-by-step order: hotspot → Private profile → firewall → ollama_setup →
   docker compose (L2) → fill env → check_health.

No placeholders; complete runnable files.
```

---

**Built and tested (2026-10-03):** see `infra/README.md` for the run order, measured numbers and gotchas
(thinking-only `qwen3:4b` tag, another app on :8000, Public lab network, tray app not spawning the server).

## 8. Mock mode
The infra has no mock of its own. With `single.env` and `MOCK=1`, the whole system runs on one laptop with no Ollama, Docker or internet. Use this on day 0 to check that every service starts.

## 9. Acceptance checklist
- [ ] `ping` works between all laptop pairs
- [ ] `curl http://<L2>:11434/api/tags` works from L1 and L3
- [ ] `curl http://<L2>:8080/v1/.well-known/ready` returns 200 from L1
- [ ] `python infra/check_health.py` shows everything ok (or degraded with a reason)
- [ ] Tokens per second recorded for each laptop and model
- [ ] The Tailscale fallback has been tried once (install, log in, ping `100.x`)
- [ ] `single.env` + `MOCK=1` starts all services on one laptop

## 10. Integration hooks
- `check_health.py --json` feeds the terminal's health page (13), or the orchestrator can proxy it at `GET /health/all`.
- If an IP changes, edit only `L*_HOST` in each laptop's `.env` and restart the services.

## 11. Sources
- Ollama FAQ (OLLAMA_HOST, KEEP_ALIVE, NUM_PARALLEL, KV cache type): https://docs.ollama.com/faq
- Ollama LAN on Windows: https://www.digitalcitizen.life/how-to-expose-ollama-to-the-network-on-windows/
- Weaviate Docker install: https://docs.weaviate.io/deploy/installation-guides/docker-installation
- Tailscale: https://tailscale.com/kb/1017/install
