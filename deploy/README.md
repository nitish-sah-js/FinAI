# 3-laptop cluster

| Laptop | Runs | Ports |
|---|---|---|
| L1 Brain | orchestrator, Ollama `qwen3:4b-instruct` | 8000, 11434 |
| L2 Compute | quant, sentiment, agri, vectordb, Weaviate (Docker), Ollama `gemma3:4b` | 8101-8104, 8080, 11434 |
| L3 Edge | ingestion, monitor, the desktop app (Next.js + Electron), Ollama `qwen3:1.7b` + `phi4-mini` | 8201, 8202, 3000, 11434 |

Every service checks a shared secret (`X-Cluster-Key`, or `?key=` on WebSockets). `/health` stays open.
Services add the key only to calls aimed at the other services, never to Ollama or outside APIs.

## Steps (on L1, this repo)

1. Put all three laptops on the same Wi-Fi or hotspot. Set the network profile to Private on each.
2. `copy deploy\cluster.env.example deploy\cluster.env`, then fill in `L1_IP`, `L2_IP` and `L3_IP` from `ipconfig`.
3. `powershell -ExecutionPolicy Bypass -File deploy\gen_cluster.ps1`
   - This rejects missing, placeholder or duplicate IPs.
   - It generates `CLUSTER_KEY` if empty.
   - It writes `deploy\out\{L1,L2,L3}.env` and `L3.env.local` (for the app).
   - It copies secrets (SMTP, Telegram, LLM keys) from this laptop's `.env`.
4. `copy deploy\out\L1.env .env` (your old `.env` holds the secrets and was already read in step 3).
5. `powershell -ExecutionPolicy Bypass -File deploy\make_bundles.ps1` builds `deploy\out\L2_bundle.zip` and `L3_bundle.zip`.
   - They contain the key and secrets. Move them by USB or a shared folder, and never post them.
6. On L2 and L3: unzip the bundle, then follow its README (`setup_Lx.ps1 -PullModels`, then `start_Lx.ps1`).
7. Start in this order: **L2, then L3, then L1** (`powershell -ExecutionPolicy Bypass -File infra\run_all_local.ps1 -Laptop L1`).
8. `powershell -ExecutionPolicy Bypass -File deploy\verify_cluster.ps1`
   - It pings every service and each laptop's Ollama.
   - It checks the key: 401 without it, accepted with it.
   - It exits 1 if any check fails.
   - The app's Cluster page shows the same thing live.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `unreachable (ConnectTimeout)` | Laptop asleep, a different Wi-Fi, Public network profile, or `infra\firewall.ps1` (Admin) not run on that laptop |
| `unreachable (ConnectError)` | Nothing is listening. Start that laptop, or check `data\logs\<service>.log.err` there |
| `KEY AUTH FAIL` | That laptop has an old `.env`. Regenerate the bundles, copy them again and restart |
| `missing gemma3:4b` | `ollama pull gemma3:4b` on that laptop. Until then its roles fall back to L1's model (marked "fallback") |
| The app shows "orchestrator is not reachable" | `apps\terminal\.env.local` on L3 points at the wrong L1 IP. Re-run step 3 and rebuild the L3 bundle |
| The IPs changed (new hotspot) | Edit `deploy\cluster.env` and repeat steps 3 to 8. Keep the same key |

To test the scripts on one laptop, run `deploy\verify_cluster.ps1 --allow-loopback` with all three IPs set to `127.0.0.1`.
