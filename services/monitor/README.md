# Monitor & tiered alerts (L3 :8202, docs/11)

Runs continuously and independently of user queries; never calls the orchestrator graph.

## Run
```bash
cd services/monitor
../../.venv/Scripts/python -m uvicorn monitor.main:app --host 0.0.0.0 --port 8202
MOCK=1 ../../.venv/Scripts/python -m uvicorn monitor.main:app --port 8202   # replays fixtures/monitor/feed_replay.jsonl + emits the two §9 alerts every 30 s
../../.venv/Scripts/python -m pytest -q tests
```
API: `WS /ws/alerts` (replays last 10, heartbeat 20 s) · `GET /alerts?limit&tier_min&since` · `POST /alerts/{id}/ack` ·
`POST /alerts/test` (demo knob, body optional) · `GET /monitor/state` · `GET /health`.

## How it works
`engine.py` tasks: feed `GET {INGEST_URL}/feed/since` 15 s · own 5-minute bars 300 s (ingestion `/prices`, else yfinance) ·
baselines (30 d of 5 m bars) hourly · weather for linked regions 30 min · agri 6 h · portfolio 5 min. Each cycle:
detectors → impact/tier (`scoring.py`) → cooldown / escalation / price+news merge (`dedupe.py`) → headline
(`writer.py`: P11 via gateway role `alert`, 1.5 s budget, numbers post-check, template fallback) → `alerts.db` → WS,
then Telegram/email for Tier 3 in the background. Price/volume detectors only look at bars ≤ 15 min old, so a closed
market never alerts on stale data. All thresholds, severities, cooldowns and intervals are in `monitor/config.yaml`.

## Config / env
`INGEST_URL`, `AGRI_URL`, `ORCH_URL`, `OLLAMA_L3`, `L3_HOST` (deep links), `MOCK`, optional `TELEGRAM_BOT_TOKEN` +
`TELEGRAM_CHAT_ID`, optional `SMTP_USER` + `SMTP_APP_PASSWORD` + `ALERT_EMAIL_TO` (root `.env`, never committed).
Region links come from `data/region_exposure.json` (09 §9 format; the 11 §13 flat format is also accepted).

## Decisions beyond the spec
- `weather.alerts` is normalised from either 10 §6 kinds or free-text IMD bulletins, plus `storm` → cyclone/hurricane.
- Weather/agri emit ONE candidate per region (all linked held tickers), so the `kind:region_id` cooldown works;
  relevance = strongest held link.
- A merge (price_z ↔ news_burst within 10 min) updates the first alert in place; it is re-broadcast only if the impact
  or tier rose. Escalations keep the same `alert_id` (clients replace by id).
- If `qwen3:1.7b` is not pulled the gateway falls back to the L1 model; a cold model exceeds 1.5 s, so the template is used.
- `QueryRequest.alert_id` (orchestrator) stores the triggering alert; "explain yourself" names it.
