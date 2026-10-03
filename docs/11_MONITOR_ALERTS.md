# 11 — Monitor & Tiered Alerts Service

| | |
|---|---|
| **Owner / Laptop** | Edge person · **L3 "Edge & UI"** · port **8202** · folder `services/monitor` |
| **Status** | ✅ Built 2026-10-03 (`services/monitor`, 28 tests). Verified live: yfinance 5-min bars for the demo book, live agri polling, WS replay/ack. Ingestion (10) not built yet: feed polling degrades, prices fall back to yfinance |
| **Depends on** | [01_CONTRACTS.md](01_CONTRACTS.md) (`Alert`, `Evidence`, `Health`). [10 Ingestion](10_DATA_INGESTION.md) (`GET /feed/since`, `/weather/features`, `/prices`). [09 Agri](09_ISRO_AGRI.md) (`/agri_signal`). [03 LLM gateway](03_LLM_GATEWAY.md) (role `alert_writer` → `qwen3:1.7b` on L3). [05 Prompts](05_RUNTIME_PROMPTS.md) (P11) |
| **Provides** | `WS /ws/alerts` (streams `Alert`), `GET /alerts`, `POST /alerts/{id}/ack`, `POST /alerts/test`, Telegram and email delivery for Tier 3 |
| **Used by** | [13 Terminal](13_FRONTEND_TERMINAL.md) (popup toasts and alert history), [14 Desktop pet](14_DESKTOP_PET.md) (nudge on Tier ≥ 1) |

The monitor runs **continuously and independently of user queries**. It never calls the orchestrator's graph. It only writes alerts whose deep link *opens* a pre-filled query in the terminal. That way, it can't burn LLM quota or block a demo run.

---

## 1. Folder layout

```
services/monitor/
├── app.py              # create_service_app("monitor"); WS /ws/alerts; REST endpoints
├── portfolio.py        # load holdings + weights; region links via data/region_exposure.json
├── detectors/
│   ├── price_volume.py # price_z, volume_z
│   ├── news_burst.py   # news_burst
│   ├── sentiment.py    # sentiment_shift
│   ├── weather.py      # weather_threshold
│   └── agri.py         # agri_stress
├── scoring.py          # impact_score, tier routing
├── dedupe.py           # cooldown + merge
├── writer.py           # P11 headline via qwen3:1.7b (+ template fallback)
├── delivery/
│   ├── ws_hub.py       # broadcast to connected WS clients
│   ├── telegram.py     # optional
│   └── email.py        # optional (SMTP)
├── store.py            # SQLite data/alerts.db
├── loop.py             # scheduler
└── tests/
```

---

## 2. Inputs

| Input | How | Interval |
|---|---|---|
| New news (with sentiment fields) and price ticks | `GET {INGEST_URL}/feed/since?ts=<last>` | 15 s |
| Weather for regions linked to holdings | `POST {INGEST_URL}/weather/features` | 30 min |
| Agri stress for linked regions | `POST {AGRI_URL}/agri_signal` | 6 h (it's a slow signal) |
| Price history for baselines | `POST {INGEST_URL}/prices` (period `30d`, interval `5m`, and `1y`/`1d`) | at startup, then hourly |
| Portfolio | `data/portfolio_demo.csv` (`ticker,qty,avg_price,sector`). The orchestrator's `GET {ORCH_URL}/portfolio/{id}` is used when available | 5 min |
| Region links | `data/region_exposure.json` (owned by 09): region_id → tickers with a link strength from 0 to 1 | at startup |

Portfolio weight: `w_i = qty_i * last_price_i / Σ_j qty_j * last_price_j`.

---

## 3. Detectors (pure functions, each returns `list[Candidate]`)

```python
class Candidate(BaseModel):
    kind: str                 # Alert.kind
    tickers: list[str]
    severity: float           # 0..1 (detector-specific normalisation below)
    relevance: float          # 0..1 how directly this touches the holding
    confidence: float         # 0..1
    facts: dict               # numbers used, passed to the writer and stored
    evidence_ids: list[str]   # ids of the Evidence fetched (if any)
```

| kind | Rule (trigger) | severity | relevance | confidence |
|---|---|---|---|---|
| `price_z` | `z = r_5m / σ_5m(20 trading days, same instrument)`, trigger `|z| ≥ 3` | `min(|z|/6, 1)` | 1.0 (direct holding) | 0.8 if ≥ 20 days of history, else 0.5 |
| `volume_z` | `z = (log V_30m − mean(log V_30m same time-of-day, 20d)) / std`, trigger `z ≥ 3` | `min(z/6, 1)` | 1.0 | 0.7 |
| `news_burst` | `k` = tagged items in last 60 min, `λ` = hourly mean over 7 d (floor 0.5). `z = (k−λ)/√λ`, trigger `z ≥ 3 and k ≥ 3` | `min(z/8, 1)` | 1.0 if ticker tagged, 0.5 if sector keyword | 0.6 |
| `sentiment_shift` | `Δ = mean(score, last 6 h) − mean(score, prior 24 h)` per ticker, score in [−1, 1] (positive = +conf, negative = −conf, neutral = 0). Trigger `|Δ| ≥ 0.4 and n_6h ≥ 3` | `min(|Δ|, 1)` | 1.0 | mean FinBERT confidence of the items |
| `weather_threshold` | `weather.alerts` non-empty for a region linked to a holding (thresholds are defined once in 10 §6) | cyclone/hurricane 0.9, heavy_rain 0.6, heatwave 0.5, rain_deficit 0.5 | `link_strength` from region_exposure.json | Evidence.confidence |
| `agri_stress` | `stress_class` becomes `stressed` or `severe` (it was healthy or watch at the previous poll) | stressed 0.6, severe 0.9 | `link_strength` | Evidence.confidence (the 09 model output) |

All thresholds live in `config.yaml` so they can be tuned live during rehearsal. **Demo knob:** `POST /alerts/test` injects a synthetic candidate so the pet and popup flow can be shown on cue.

---

## 4. Impact scoring and tiers (`scoring.py`)

```python
def impact_score(c: Candidate, weights: dict[str, float]) -> float:
    exposure = min(1.0, sum(weights.get(t, 0.0) for t in c.tickers) / 0.25)  # 25%+ of book → full exposure
    return round(min(1.0, c.severity * c.relevance * (0.4 + 0.6 * exposure)), 3)

def tier(impact: float, confidence: float) -> int:
    if impact >= 0.6 and confidence >= 0.6: return 3   # popup + Telegram/email
    if impact >= 0.3:                       return 2   # popup with one-line reason
    return 1                                           # pet nudge only
```
Any candidate with `impact < 0.1` is dropped. Tickers not held are monitored only when they're in the watchlist (`relevance × 0.3`).

---

## 5. Dedupe and cooldown (`dedupe.py`)

- `cooldown_key = f"{kind}:{ticker}"` (for weather and agri: `f"{kind}:{region_id}"`).
- Cooldown windows: price/volume 30 min, news_burst 60 min, sentiment_shift 120 min, weather 6 h, agri 24 h.
- Inside the window, a new candidate **updates** the existing alert (facts, impact) without re-broadcasting, **unless its tier is higher**, in which case it's re-broadcast as an escalation (`reason` is prefixed with "Escalated:").
- Merge across kinds: if a `price_z` and a `news_burst` hit the same ticker within 10 min, emit one alert of the higher-impact kind and put both facts in `reason`.

---

## 6. Alert writer (P11) (`writer.py`)

- Uses `copilot_llm.chat(role="alert_writer", ...)`, routed to `qwen3:1.7b` on `OLLAMA_L3`, with `think:false`, temperature 0.2 and `max_tokens` 60.
- Input is only the `facts` dict, the ticker and the kind. The prompt is P11 from 05 (shared ground rules plus: "One sentence, under 25 words: what happened, which holding, why it matters, confidence.").
- **Post-check in code:** fewer than 25 words, and every number in the headline must appear in `facts` (the same regex as the validator in 04). If the check fails or the LLM is down, use a **template**:
  `f"{ticker}: {kind_label} ({key_fact}); {weight:.0%} of portfolio; confidence {conf_label}."`
- Latency budget is 1.5 s. Template fallback is instant, so alerts are never delayed by the LLM.

---

## 7. Alert build and delivery

```python
alert = Alert(
  alert_id=new_alert_id(), tier=tier(impact, c.confidence), kind=c.kind, tickers=c.tickers,
  headline=writer_output, reason=reason_from_facts(c), impact_score=impact, confidence=c.confidence,
  evidence_ids=c.evidence_ids, created_at=utcnow(), cooldown_key=key,
  deeplink=f"http://{L3_HOST}:3000/run/new?q={urlencode(suggested_query(c))}&alert={alert_id}")
```
`suggested_query` examples: `"How does the Odisha cyclone affect my portfolio over 5 days?"`, `"Why did ONGC.NS spike and should I hedge?"`.

| Tier | WS `/ws/alerts` | Terminal | Pet | Telegram | Email |
|---|---|---|---|---|---|
| 1 | yes | history only | nudge (bounce) | no | no |
| 2 | yes | toast with headline + reason + "Analyze" button | alert state | no | no |
| 3 | yes | toast (sticky) | alert state + sound | yes (if configured) | yes (if configured) |

- **Telegram (optional):** `POST https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage` with `{"chat_id": TELEGRAM_CHAT_ID, "text": "...", "parse_mode": "HTML", "disable_web_page_preview": true}`. Create the bot with @BotFather, send it one message, then read the chat_id from `getUpdates`. Rate limit is 1 message per 3 s, with a queue.
- **Email (optional):** `smtplib.SMTP_SSL("smtp.gmail.com", 465)` with an app password from `.env` (`SMTP_USER`, `SMTP_APP_PASSWORD`, `ALERT_EMAIL_TO`). Never commit credentials.
- Delivery failures are logged and set `/health` to `degraded`, but never block the WS broadcast.

---

## 8. REST and WS API

| Method | Path | Body / query | Returns |
|---|---|---|---|
| WS | `/ws/alerts` | — | On connect, the last 10 alerts as `{"type":"alert","data":Alert,"replay":true}`, then live alerts `{"type":"alert","data":Alert}` and `{"type":"heartbeat","ts":...}` every 20 s |
| GET | `/alerts` | `?limit=50&tier_min=1&since=` | `list[Alert]` |
| POST | `/alerts/{alert_id}/ack` | — | `Alert` with `acknowledged=true` (broadcast `{"type":"ack","alert_id":...}`) |
| POST | `/alerts/test` | `{"kind":"weather_threshold","tickers":["ONGC.NS"],"tier":3}` | `Alert` (demo injection, marked `reason` "TEST") |
| GET | `/monitor/state` | — | detectors, last run times, active cooldowns, counts (for the 13 health page) |
| GET | `/health` | — | `Health` (deps: ingestion, agri, ollama_l3, telegram) |

SQLite `data/alerts.db`, table `alerts(alert_id PK, json TEXT, created_at, tier, kind, cooldown_key, acknowledged)`.

---

## 9. Example outputs

Tier 3 cyclone alert:
```json
{"type":"alert","data":{
  "alert_id":"al_20261003_3fa21c","tier":3,"kind":"weather_threshold",
  "tickers":["ADANIPORTS.NS","ONGC.NS"],
  "headline":"Very severe cyclone 310 km off Puri threatens Paradip; ports and ONGC exposure is 22% of portfolio; confidence medium.",
  "reason":"OD-Puri alerts [cyclone, heavy_rain]; rain +184% vs normal; link strength ADANIPORTS 0.8, ONGC 0.5",
  "impact_score":0.66,"confidence":0.75,"evidence_ids":["ev_weather_001"],
  "created_at":"2026-10-03T08:46:10Z","cooldown_key":"weather_threshold:OD-Puri",
  "deeplink":"http://192.168.43.103:3000/run/new?q=How+does+the+Odisha+cyclone+affect+my+portfolio+over+5+days%3F&alert=al_20261003_3fa21c",
  "acknowledged":false}}
```

Tier 1 news burst:
```json
{"type":"alert","data":{"alert_id":"al_20261003_09bd77","tier":1,"kind":"news_burst","tickers":["ITC.NS"],
 "headline":"ITC.NS: 5 headlines in the last hour versus about 1 normally; 6% of portfolio; confidence medium.",
 "reason":"k=5, lambda=0.9, z=4.3","impact_score":0.24,"confidence":0.6,"evidence_ids":[],
 "created_at":"2026-10-03T09:02:00Z","cooldown_key":"news_burst:ITC.NS",
 "deeplink":"http://192.168.43.103:3000/run/new?q=What+is+the+news+on+ITC+and+does+it+matter%3F","acknowledged":false}}
```

---

## 10. Mock mode
`MOCK=1`: detectors read `fixtures/monitor/feed_replay.jsonl`, and the service emits the two alerts above on a 30 s loop, so the pet (14) and terminal (13) can be built with nothing else running.

---

## 11. Build prompt (copy-paste into a coding LLM)

```
Build a FastAPI "monitor" microservice (Python 3.11, httpx, aiosqlite, pydantic v2) for a market-alert system.
Use the shared package copilot_common (Alert, Health, create_service_app, new_alert_id, settings) and
copilot_llm.chat(role="alert_writer", messages=..., max_tokens=60). I paste their signatures below.

Step 1. app.py: app = create_service_app("monitor", deps_check=...). Lifespan starts loop.run().
Step 2. portfolio.py: load_portfolio() from data/portfolio_demo.csv (or GET {ORCH_URL}/portfolio/demo if 200),
        weights(prices) -> dict[ticker, float]; region_links() from data/region_exposure.json -> dict[region_id, list[(ticker, strength)]].
Step 3. detectors/*.py: implement EXACTLY the six rules in the spec table (section 3) as pure functions
        taking pandas/series or lists and returning list[Candidate]. Thresholds from config.yaml.
Step 4. scoring.py: impact_score() and tier() exactly as specified.
Step 5. dedupe.py: CooldownManager with per-kind windows, update-without-rebroadcast, escalation on higher tier,
        cross-kind merge within 10 minutes for the same ticker.
Step 6. writer.py: write_headline(candidate, weight) -> str using P11; post-check (<25 words, every number in facts);
        template fallback; 1.5 s timeout.
Step 7. delivery/ws_hub.py: connection set, broadcast(json), replay last 10 on connect, heartbeat 20 s.
        delivery/telegram.py + email.py: optional, enabled only if env vars are set; async queue, never raise.
Step 8. store.py: SQLite alerts table; save/update/list/ack.
Step 9. loop.py: tasks: feed poll 15 s (GET {INGEST_URL}/feed/since), weather 30 min, agri 6 h, baselines hourly.
        Each cycle: detectors → candidates → impact/tier → dedupe → writer → Alert → store → deliver.
Step 10. Endpoints: WS /ws/alerts, GET /alerts, POST /alerts/{id}/ack, POST /alerts/test, GET /monitor/state.
Step 11. MOCK=1: replay fixtures/monitor/feed_replay.jsonl; also emit the two example alerts every 30 s.
Step 12. tests: each detector with synthetic data (trigger and no-trigger cases), scoring table, cooldown
        suppression and escalation, writer fallback when the LLM raises, WS client receives a test alert.
Complete files, no TODOs.
[PASTE 01_CONTRACTS.md §5 Alert model + this file §2–9]
```

---

## 12. Acceptance checklist
- [ ] `POST /alerts/test` makes a WS client receive the alert within 1 s, and it is stored in `alerts.db`
- [ ] The same weather trigger polled twice within 6 h gives exactly one broadcast; a higher tier gives an escalation
- [ ] With the LLM down, headlines come from the template and alerts are not delayed
- [ ] Tier 3 sends Telegram when the token is set and silently skips otherwise
- [ ] With ingestion down, `/health` reports `degraded` and the service keeps running
- [ ] Detector unit tests pass, including the no-trigger cases

## 13. Integration hooks
- **10 Ingestion:** feed items must carry `news_id, title, tickers, published_at, sentiment_label, sentiment_score, sentiment_confidence` (merged by the ingestion worker before buffering), and price ticks carry `{ticker, ts, close, volume}`.
- **09 Agri:** `region_exposure.json` format `{"MH-Yavatmal": [{"ticker":"UPL.NS","strength":0.6,"crop":"cotton"}], ...}`. Shared file, and 09 owns it.
- **13 Terminal:** subscribes to `ws://{L3_HOST}:8202/ws/alerts`, shows toasts, and the "Analyze" button opens `deeplink`. The health page reads `/monitor/state`.
- **14 Pet:** subscribes to the same WS. Tier 1 = nudge, Tier ≥ 2 = alert state, and a click opens `deeplink`.
- **04 Orchestrator:** none at runtime. When a run starts from a deep link, the `alert` query parameter is stored with the run so "explain yourself" can say what triggered it.

## 14. Sources
- [Telegram Bot API `sendMessage`](https://core.telegram.org/bots/api#sendmessage)
- [Ollama thinking control (`think:false`)](https://docs.ollama.com/capabilities/thinking)
- Python `smtplib` docs (https://docs.python.org/3/library/smtplib.html)
