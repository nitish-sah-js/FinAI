# infra: 3 laptops on one network (docs/02)

Do the steps in this order. Each one has a check, so you know it worked before moving on.

| # | Step | Command (PowerShell, repo root) | Check |
|---|---|---|---|
| 1 | **One network for all 3 laptops.** Phone hotspot (best), travel router, or Windows Mobile Hotspot on one laptop. Avoid lab/college Wi-Fi: it is usually Public and often isolates clients | Settings → Network → Mobile hotspot | `ipconfig \| findstr IPv4` on each laptop |
| 2 | **Network profile = Private** on every laptop | Settings → Network & internet → (network) → Private | `powershell -File infra\firewall.ps1 -Check` |
| 3 | **Firewall rules** (Admin PowerShell, every laptop) | `powershell -ExecutionPolicy Bypass -File infra\firewall.ps1` | `-Check` lists `Copilot-Hackathon` + `Copilot-ICMP` |
| 4 | **Ollama** on every laptop | `powershell -ExecutionPolicy Bypass -File infra\ollama_setup.ps1 -Laptop L1` (L2, L3) | prints tok/s; `curl http://<ip>:11434/api/tags` from another laptop |
| 5 | **Weaviate** (L2 only; Docker Desktop must be running) | `docker compose -f infra/docker-compose.yml up -d` | `curl http://localhost:8080/v1/.well-known/ready` → 200 |
| 6 | **Env files**: put the 3 real IPs in, then activate the right one | `python infra/make_env.py --hosts <L1> <L2> <L3>` then `powershell -File infra\use_env.ps1 -Laptop L1` | `.env` shows `THIS_LAPTOP=L1` |
| 7 | **LAN test** (from each laptop) | `powershell -ExecutionPolicy Bypass -File infra\lan_test.ps1 -FromEnv` | every port `open` (only for services that are started) |
| 8 | **Health table** | `.venv\Scripts\python infra\check_health.py` (`--watch`, `--json`, `--require a,b`) | all `ok` / `degraded` with a reason |

Single-laptop demo fallback: `powershell -File infra\use_env.ps1 -Laptop single` (all 127.0.0.1, `MOCK=1`).

## Files
| File | What |
|---|---|
| `env/L1.env L2.env L3.env single.env` | identical keys (generated from `.env.example` by `make_env.py`); only the 3 `*_HOST` lines and `MOCK` differ |
| `make_env.py` / `use_env.ps1` | regenerate the env files with real IPs / copy one to `.env` (backs up the old one) |
| `firewall.ps1` | Admin: opens 8000, 8101-8104, 8201-8202, 3000, 8080, 50051, 11434 + ping on **Private** only. `-Check` (read-only), `-Remove` |
| `ollama_setup.ps1` | `-Laptop L1\|L2\|L3`: user env vars, restarts Ollama, pulls that laptop's models, benchmarks tok/s. `-Backup`, `-SkipPull`, `-DryRun` |
| `docker-compose.yml` | Weaviate 1.39.8, own vectors (`vectorizer none`), HTTP 8080 + gRPC 50051, container healthcheck |
| `lan_test.ps1` | 1-second TCP probe of every service port on each laptop + the 3 likely causes when something is closed |
| `check_health.py` | every `/health`, Ollama `/api/tags` (with missing-model detection), Weaviate ready; detects a port taken by another app |
| `tests/` | `python -m pytest infra/tests` |

## Measured on this laptop (L1), 2026-10-03
| Laptop | GPU | Model | Generate | Prompt | VRAM loaded |
|---|---|---|---|---|---|
| L1 | RTX 4050 Laptop 6 GB | `qwen3:4b-instruct` (Q4_K_M, ctx 8192, q8_0 KV, 2 parallel) | **62.7 tok/s** | 1,353 tok/s | 3.6 GB |
| L2 | | `gemma3:4b` | _fill in_ | | |
| L3 | | `qwen3:1.7b`, `phi4-mini` | _fill in_ | | |

The first request after Ollama starts takes ~40 s (model load). The orchestrator and CLI warm the model up on start.

## Gotchas found while testing (read these)
- **`qwen3:4b` is the thinking-only build.** It cannot turn thinking off and writes its reasoning into the answer. Use `qwen3:4b-instruct` (already in `ollama_setup.ps1` and the env files).
- **`num_ctx` cannot be set through the OpenAI endpoint.** `OLLAMA_CONTEXT_LENGTH=8192` does it server-side (set by `ollama_setup.ps1`).
- **Another app on port 8000.** Docker Desktop auto-starts other projects' containers. Here `vl_scraper` (visa-lead-platform) published `:8000` on IPv6, so `localhost:8000` (browsers try `::1` first) reached the scraper, not the orchestrator. `check_health.py` now reports this as `port taken by another app`. Before the demo, run `docker ps` and stop anything else on our ports (`docker stop vl_scraper`), or use `127.0.0.1` / the LAN IP rather than `localhost`.
- **The Ollama tray app may not start its server** when launched from a script. `ollama_setup.ps1` falls back to a hidden `ollama serve`, which runs until reboot. After a reboot, the tray app starts normally with the saved user variables.
- **Lab network CCC_LAB_01_A is Public** (Wi-Fi and Ethernet) and probably domain-managed. Inbound LAN traffic is blocked there, so use a phone hotspot or Tailscale for the 3 laptops.
- **Windows refuses a closed port slowly (~2 s).** The services remember down hosts for 30 s, so a missing laptop costs that delay once, not on every call.
