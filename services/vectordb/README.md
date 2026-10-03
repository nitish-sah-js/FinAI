# Vector DB & historical analogs on L2:8104 (docs/08)

```powershell
# once (repo root): GPU torch, then the service deps
.venv\Scripts\python -m pip install torch --index-url https://download.pytorch.org/whl/cu130
.venv\Scripts\python -m pip install -r services/vectordb/requirements.txt

cd services\vectordb
..\..\.venv\Scripts\python scripts\build_events.py          # seed CSV -> live yfinance outcomes -> data/historical_events.json
..\..\.venv\Scripts\python scripts\calibrate_conformal.py   # leave-one-out -> data/conformal_q.json
..\..\.venv\Scripts\python -m uvicorn vectordb.main:app --host 0.0.0.0 --port 8104
..\..\.venv\Scripts\python -m pytest tests -q               # 125 tests, Weaviate not needed

# when Docker is up (L2):
docker compose -f ..\..\infra\docker-compose.yml up -d weaviate
..\..\.venv\Scripts\python scripts\seed_weaviate.py --reset # embed + insert the 40 events (idempotent, uuid5(event_id))
```
Set `EMBED_DEVICE=cuda` in `.env` to run bge-small on the GPU. The default is `cpu`, which takes about 10 ms per query.

## Data files (all under `copilot_common.settings.get_data_dir()`, never the CWD)
| file | owner | content |
|---|---|---|
| `data/events_seed.csv` | 08 | 40 hand-written events with CSV quoting. `build_events.py` validates the type, split, dates, tickers and column count |
| `data/historical_events.json` | 08 (generated) | **The analog corpus.** Not `events.json`: that name belongs to the backtest |
| `data/conformal_q.json` | 08 (generated) | q̂ per horizon × event type (× measure), `n_calib` = number of events |
| `data/events.json` | **12 (backtest)** | The 8 holdout cases. This service only reads it, and unions its ids into the holdout filter |

## Behaviour
- **Backends.** Weaviate hybrid search when it is reachable. Otherwise brute-force cosine in numpy over the cached corpus vectors (`degraded_reason="weaviate_down_numpy_fallback"`, confidence −0.1). A circuit breaker (`copilot_common.reachability`) and a background prober running every 15 s mean a request never pays a connect attempt. Measured fallback latency: 17 ms server-side, 19 ms end-to-end with curl.
- **Hard filters, never relaxed.**
  - `exclude_holdout`: `split=="train"` and `event_id ∉` (the 08 §4.2 list ∪ `data/events.json`).
  - `as_of`: `event_date < as_of`, and an analog's outcome is used only if its horizon window closed before `as_of`. `as_of` accepts a date, a datetime or an ISO string.
- **Soft filters.** `event_type` and severity ±0.3, relaxed in that order (severity first, then type) when fewer than 3 analogs remain. Every relaxation is listed in `filters_relaxed` and costs 0.1 confidence. Intent short names are mapped to corpus types (`oil`→`oil_shock`, `rates`→`rate_shock`, `monsoon`→`monsoon_deficit`).
- **Similarity.** Our own cosine with a cut-off of 0.5. A *specificity guard* also applies: if the query's best match is less than 0.10 above its median similarity to the corpus, no analogs are returned. bge-small scores nonsense at 0.45–0.55 against everything. On the 40-event corpus, real queries scored ≥ 0.129 above the median and nonsense ≤ 0.088. If the model cannot load, a hashing embedder with raw cosine (threshold 0.2) is used and the evidence is degraded with confidence ≤ 0.2. There is no rescaling.
- **Distribution.** There is one measure per asset. Factor assets (`^NSEI, CL=F, BZ=F, INR=X`) are always `raw`, because quant's scenario_builder reads only raw for them. Equities are `abnormal` when every contributing analog has it, otherwise `raw` with a `measure_note`. When an analog lacks the exact ticker, a proxy-group member stands in (`proxy_group`, `proxies_used`, `n_exact`). Weights are sim²/Σsim², and the median/p10/p90 are weighted quantiles. The conformal band is `median ± q̂`. If `n_calib < 8`, the warning `conformal interval unreliable (n<8)` is added.
- **News.** `/news/index` dedupes inside the batch and against the store (uuid5). It stamps `indexed_at` after `insert_many` returns, counts per-object insert errors, and keeps a `None` sentiment as `None`. When Weaviate is down, items go to an in-memory store (5,000 items) that `/news/search` can query, and the evidence is degraded `news`, not an error.
- **MOCK=1.** Returns `copilot_common/fixtures/vectordb/{find_analogs,news_index,news_search}.json`. No model is loaded.

## Outcomes (built live 2026-10-03)
There is no synthetic path. Missing data means the asset is skipped and logged in the event's `outcomes_skipped`.
- 40 events, 114 outcomes. 2 were skipped: `CL=F` for `wti_negative_2020` and for `hurricane_laura_2020`, because a price ≤ 0 makes percent returns undefined.
- Yahoo has no history for the `^CNX*` sector indices, so these proxies are used and recorded in the outcome (`price_symbol`, `proxy_note`): `^CNXFMCG`→`HINDUNILVR.NS`, `^CNXENERGY`→`RELIANCE.NS`, `^CNXAUTO`→`MARUTI.NS`.
- For the benchmark itself (`^NSEI`), the abnormal returns are `null` and β = 1.
- Two seed dates were corrected to the first trading reaction:
  - `covid_lockdown_2020` → 2020-03-25 (announced 20:00 IST on 03-24).
  - `wheat_export_ban_2022` → 2022-05-16 (notified late on Friday 05-13).

## Not tested
Everything that talks to a live Weaviate is untested, because Docker was down during integration: `client.get_client` connect, `schema.create_collections`, `seed_weaviate.py`, `analogs.weaviate_search` (hybrid with server-side filters, then re-checked in Python), and the Weaviate branches of `news.py`. The filter objects are built in a unit test.
