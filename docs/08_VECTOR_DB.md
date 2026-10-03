# 08 — Vector DB & Historical Analogs (L2 :8104 + Weaviate :8080/50051)

| | |
|---|---|
| **Owner / Laptop** | Data/ML person B · **L2 "Quant & ML"** · service port **8104** · folder `services/vectordb` · Weaviate in Docker on L2 |
| **Status** | ✅ Built 2026-10-03 (`services/vectordb`, 125 tests). Corpus = `data/historical_events.json` (40 events, outcomes rebuilt live from yfinance; `data/events.json` is the backtest's 8 holdout events, 12). numpy fallback verified; the Weaviate path is untested until Docker is up (`scripts/seed_weaviate.py --reset`) |
| **Depends on** | `copilot_common` (01). Weaviate (Docker, set up in 02). `sentence-transformers` with `BAAI/bge-small-en-v1.5`. yfinance (for the seeding script only). Optional sentiment labels from 07 for news |
| **Provides** | Tool `analogs` (`POST /find_analogs`), `POST /news/index`, `POST /news/search`, `POST /embed` (utility), `GET /latency` (index-latency stats) |
| **Called by** | Orchestrator `analog_agent` (04). Ingestion news indexer (10). Backtest (12), which uses `exclude_holdout` and `as_of`. Terminal latency panel (13) |

> **Why this module matters.** It turns "a hurricane is coming" into "here are 5 similar past events and the **range** of what happened to these assets", as a distribution rather than a single number. Never cut it.

---

## 1. Folder layout
```
services/vectordb/
├── vectordb/
│   ├── main.py            # create_service_app("vectordb", deps_check=weaviate_ready)
│   ├── client.py          # get_client() → weaviate v4 client (connect_to_local)
│   ├── embed.py           # Embedder (bge-small, normalize, batch); text templates
│   ├── schema.py          # create_collections(reset=False)
│   ├── analogs.py         # find_analogs(...) → value dict
│   ├── stats.py           # weighted_quantile, conformal interval, confidence rule
│   └── news.py            # index_news(items) / search_news(...)
├── scripts/
│   ├── build_events.py    # data/events_seed.csv → compute outcomes via yfinance → data/historical_events.json
│   ├── seed_weaviate.py   # data/historical_events.json → embed → insert
│   └── calibrate_conformal.py  # leave-one-out residuals → data/conformal_q.json
├── tests/ test_stats.py test_analogs.py test_templates.py
└── requirements.txt   # fastapi uvicorn weaviate-client>=4.9 sentence-transformers yfinance pandas numpy
data/events_seed.csv   # hand-written (§4)
data/historical_events.json       # generated
data/conformal_q.json  # generated
```

## 2. Weaviate setup
Run Weaviate on L2. The full compose file is in 02. This is the minimum:
```yaml
services:
  weaviate:
    image: cr.weaviate.io/semitechnologies/weaviate:1.32.0   # pin a recent 1.x; check docs for latest
    ports: ["8080:8080", "50051:50051"]
    environment:
      AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED: "true"
      PERSISTENCE_DATA_PATH: /var/lib/weaviate
      DEFAULT_VECTORIZER_MODULE: none
      CLUSTER_HOSTNAME: node1
    volumes: ["weaviate_data:/var/lib/weaviate"]
volumes: { weaviate_data: {} }
```
Python client (v4):
```python
import weaviate
from weaviate.classes.config import Configure, Property, DataType, VectorDistances
from weaviate.classes.query import Filter, MetadataQuery

client = weaviate.connect_to_local(host=settings.WEAVIATE_HOST, port=8080, grpc_port=50051)
client.collections.create(
    "HistoricalEvent",
    vector_config=Configure.Vectors.self_provided(          # older 4.x: vectorizer_config=Configure.Vectorizer.none()
        vector_index_config=Configure.VectorIndex.hnsw(distance_metric=VectorDistances.COSINE)),
    properties=[...])                                        # §3
```
Vectors come from our own embedder: `BAAI/bge-small-en-v1.5`, **384 dimensions**, cosine distance, `normalize_embeddings=True`.

## 3. Collections

### 3.1 `HistoricalEvent` (about 40 records)
| property | type | notes |
|---|---|---|
| `event_id` | TEXT (skip vectorize) | `hurricane_ida_2021` |
| `title` | TEXT | |
| `event_type` | TEXT | `hurricane|cyclone|monsoon_deficit|heatwave|rate_shock|oil_shock|policy` |
| `region` | TEXT | `Gulf of Mexico`, `Odisha`, `All-India` |
| `country` | TEXT | `US`, `IN`, `Global` |
| `start_date`, `end_date`, `event_date` | DATE | `event_date` = day zero for returns (landfall, announcement) |
| `severity_value` | NUMBER | |
| `severity_unit` | TEXT | `Saffir-Simpson category`, `IMD class (1-7)`, `rain deficit %`, `bps`, `oil move %` |
| `severity_norm` | NUMBER | 0–1, normalized (§3.3) |
| `description` | TEXT | 3–5 sentences |
| `mechanism` | TEXT | how it transmits to prices |
| `affected_assets` | TEXT_ARRAY | `["refiners","crude","natgas"]` |
| `tickers` | TEXT_ARRAY | the assets the outcomes were computed for |
| `outcomes_json` | TEXT | JSON string of the `outcomes` list (keeps the schema simple) |
| `split` | TEXT | `train` or `holdout` (§4.2) |
| `source`, `source_url` | TEXT | |
| `embedding_text` | TEXT | the exact text that was embedded (for debugging) |

`outcomes` (one object per ticker):
```json
{"asset":"NG=F","benchmark":"SPY","ret_1d":0.021,"ret_5d":0.064,"ret_20d":0.11,
 "abnormal_1d":0.015,"abnormal_5d":0.041,"abnormal_20d":0.07,"beta_used":0.32}
```

### 3.2 `NewsItem` (live)
Properties are `news_id, title, summary, source, url, published_at (DATE), tickers (TEXT_ARRAY), sentiment_label, sentiment_score (NUMBER), ingested_at (DATE), indexed_at (DATE)`. The vector is computed from `title + ". " + summary`.

### 3.3 Embedding text template (identical for corpus and query)
```
{event_type} {region} severity {severity_value} {severity_unit}. {description} Mechanism: {mechanism}
```
- **Query side.** The analog agent builds a *situation description* from live evidence with the same template. For example: `cyclone Odisha severity 5 IMD class (1-7). Very severe cyclonic storm tracking toward Odisha coast, landfall expected in 48h, heavy rain over ports and agri districts. Mechanism: port shutdowns, crop damage, power outages`.
- Do **not** add the bge "Represent this sentence…" instruction prefix. This is symmetric event-to-event matching, so both sides are embedded the same way.
- `severity_norm` is computed as follows:
  - hurricane: cat/5
  - cyclone: IMD class/7 (D=1, DD=2, CS=3, SCS=4, VSCS=5, ESCS=6, SuCS=7)
  - monsoon: min(|deficit%|/30, 1)
  - heatwave: min(anomaly °C/6, 1)
  - rate: min(|bps|/100, 1)
  - oil: min(|move%|/20, 1)
  - policy: a manual 0–1 value

## 4. Seed events (`data/events_seed.csv`)
**Verify every date against the cited source before generating outcomes.** Dates below are landfall or announcement dates as commonly reported. Indian tickers use `.NS`, and Indian indices use Yahoo symbols such as `^NSEI`, `^NSEBANK`, `^CNXFMCG`, `^CNXENERGY` and `^CNXAUTO`. Some symbols have short or patchy history on Yahoo, so the script must skip them gracefully.

| # | event_id | type | event_date | region | severity | assets for outcomes | split |
|---|---|---|---|---|---|---|---|
| 1 | hurricane_katrina_2005 | hurricane | 2005-08-29 | Gulf of Mexico | 3 (landfall) | CL=F, NG=F, RB=F, XLE, VLO | train |
| 2 | hurricane_rita_2005 | hurricane | 2005-09-24 | Gulf of Mexico | 3 | CL=F, NG=F, RB=F, XLE | train |
| 3 | hurricane_gustav_2008 | hurricane | 2008-09-01 | Gulf of Mexico | 2 | CL=F, NG=F, XLE | train |
| 4 | hurricane_ike_2008 | hurricane | 2008-09-13 | Gulf (Texas) | 2 | CL=F, RB=F, VLO | train |
| 5 | hurricane_harvey_2017 | hurricane | 2017-08-25 | Gulf (Texas) | 4 | CL=F, RB=F, NG=F, VLO | train |
| 6 | hurricane_laura_2020 | hurricane | 2020-08-27 | Gulf (Louisiana) | 4 | CL=F, NG=F, XLE | train |
| 7 | **hurricane_ida_2021** | hurricane | 2021-08-29 | Gulf (Louisiana) | 4 | CL=F, NG=F, RB=F, VLO | **holdout** |
| 8 | hurricane_ian_2022 | hurricane | 2022-09-28 | Florida | 4 | CL=F, ALL, TRV | train |
| 9 | hurricane_helene_2024 | hurricane | 2024-09-26 | Florida Big Bend | 4 | CL=F, NG=F, ALL | train |
| 10 | hurricane_milton_2024 | hurricane | 2024-10-09 | Florida | 3 | CL=F, ALL, TRV | train |
| 11 | cyclone_phailin_2013 | cyclone | 2013-10-12 | Odisha | 6 (ESCS) | ^NSEI, ^CNXFMCG, INR=X | train |
| 12 | cyclone_hudhud_2014 | cyclone | 2014-10-12 | Andhra (Vizag) | 6 | ^NSEI, ^CNXENERGY | train |
| 13 | cyclone_vardah_2016 | cyclone | 2016-12-12 | Tamil Nadu (Chennai) | 5 | ^NSEI, ^CNXAUTO | train |
| 14 | cyclone_ockhi_2017 | cyclone | 2017-11-30 | Kerala/TN | 5 | ^NSEI | train |
| 15 | cyclone_fani_2019 | cyclone | 2019-05-03 | Odisha | 6 | ^NSEI, ONGC.NS, ^CNXFMCG | train |
| 16 | cyclone_amphan_2020 | cyclone | 2020-05-20 | West Bengal | 7 (SuCS peak) | ^NSEI, ITC.NS, ^CNXFMCG | train |
| 17 | cyclone_nisarga_2020 | cyclone | 2020-06-03 | Maharashtra | 4 | ^NSEI, RELIANCE.NS | train |
| 18 | cyclone_tauktae_2021 | cyclone | 2021-05-17 | Gujarat / Bombay High | 6 | ONGC.NS, RELIANCE.NS, ADANIPORTS.NS | train |
| 19 | cyclone_yaas_2021 | cyclone | 2021-05-26 | Odisha/WB | 5 | ^NSEI, ^CNXFMCG | train |
| 20 | **cyclone_biparjoy_2023** | cyclone | 2023-06-15 | Gujarat (Kutch) | 5 | ADANIPORTS.NS, RELIANCE.NS, ^NSEI | **holdout** |
| 21 | **cyclone_michaung_2023** | cyclone | 2023-12-05 | TN/Andhra (Chennai) | 4 | ^CNXAUTO, ASHOKLEY.NS, TVSMOTOR.NS | **holdout** |
| 22 | cyclone_dana_2024 | cyclone | 2024-10-25 | Odisha | 4 | ^NSEI, ^CNXFMCG | train |
| 23 | monsoon_deficit_2009 | monsoon_deficit | 2009-08-01* | All-India | −22% | ^NSEI, ^CNXFMCG, M&M.NS, INR=X | train |
| 24 | monsoon_deficit_2014 | monsoon_deficit | 2014-08-01* | All-India | −12% | ^CNXFMCG, M&M.NS, UPL.NS | train |
| 25 | **monsoon_deficit_2015** | monsoon_deficit | 2015-08-01* | All-India | −14% | ^CNXFMCG, M&M.NS, CHAMBLFERT.NS | **holdout** |
| 26 | monsoon_aug_dry_2023 | monsoon_deficit | 2023-09-01* | All-India (Aug −36%) | −36% (Aug) | ^CNXFMCG, M&M.NS, HINDUNILVR.NS | train |
| 27 | **heatwave_2022** | heatwave | 2022-04-28* | North/Central India | +4.5 °C | ^NSEI, NTPC.NS, COALINDIA.NS | **holdout** |
| 28 | heatwave_2024 | heatwave | 2024-05-29* | North India | +5 °C | NTPC.NS, TATAPOWER.NS, VOLTAS.NS | train |
| 29 | taper_tantrum_2013 | rate_shock | 2013-05-22 | Global/US | ~100 bps 10y | INR=X, ^NSEBANK, ^NSEI | train |
| 30 | rbi_emergency_cut_2020 | rate_shock | 2020-03-27 | India | −75 bps | ^NSEBANK, ^NSEI | train |
| 31 | **rbi_offcycle_hike_2022** | rate_shock | 2022-05-04 | India | +40 bps | ^NSEBANK, HDFCBANK.NS, ^NSEI | **holdout** |
| 32 | **abqaiq_attack_2019** | oil_shock | 2019-09-16 (1st trading day) | Saudi Arabia | +15% Brent | BZ=F, ONGC.NS, BPCL.NS, INR=X | **holdout** |
| 33 | wti_negative_2020 | oil_shock | 2020-04-20 | US | −300% WTI front | CL=F, ONGC.NS | train |
| 34 | ukraine_invasion_2022 | oil_shock | 2022-02-24 | Europe | +8% Brent | BZ=F, ^NSEI, INR=X, GC=F | train |
| 35 | opec_surprise_cut_2023 | oil_shock | 2023-04-03 | Global | +6% Brent | BZ=F, ONGC.NS, BPCL.NS | train |
| 36 | demonetisation_2016 | policy | 2016-11-09 | India | 0.9 | ^NSEI, ^CNXAUTO, ^NSEBANK | train |
| 37 | corp_tax_cut_2019 | policy | 2019-09-20 | India | 0.7 | ^NSEI, ^NSEBANK | train |
| 38 | covid_lockdown_2020 | policy | 2020-03-24 | India | 1.0 | ^NSEI, ^NSEBANK, INR=X | train |
| 39 | wheat_export_ban_2022 | policy | 2022-05-13 | India | 0.5 | ITC.NS, ^CNXFMCG | train |
| 40 | **rice_export_ban_2023** | policy | 2023-07-20 | India | 0.6 | KRBL.NS, LTFOODS.NS | **holdout** |

\* For monsoon and heatwave, `event_date` = the date the deficit or heatwave became public (IMD press release or bulletin). **Look up and correct the exact date.**

### 4.2 Holdout (backtest, 8 events)
The holdout events are `hurricane_ida_2021, cyclone_biparjoy_2023, cyclone_michaung_2023, monsoon_deficit_2015, heatwave_2022, rbi_offcycle_hike_2022, abqaiq_attack_2019, rice_export_ban_2023`. At least one comes from every type except policy and US-only cases.

- When `exclude_holdout=true`, which the backtest (12) always sets, `find_analogs` adds the filter `split == "train"`.
- When `as_of` is set, it also adds `event_date < as_of`, so there is no look-ahead.
- The live demo uses all events.

## 5. Outcome computation (`scripts/build_events.py`)
For each row and ticker:
1. Download daily adjusted closes from `event_date − 200d` to `event_date + 40d`.
2. `t0` = last trading close **before** `event_date`. This is the base, so the event-day reaction is included.
3. `ret_k = P(t0+k)/P(t0) − 1` for k ∈ {1, 5, 20} trading days.
4. `benchmark` = `^NSEI` for Indian tickers, `SPY` otherwise, and `^NSEI` for INR=X.
5. `beta_used` = OLS beta of the ticker on the benchmark over [t0−130, t0−10] trading days.
6. `abnormal_k = ret_k − beta_used × bench_ret_k`.
7. Skip a ticker (and log it) if there are fewer than 100 estimation days or any return is missing.
8. Write `data/historical_events.json` as a list of `HistoricalEvent` dicts with `outcomes`.

## 6. `find_analogs` algorithm
```python
class AnalogReq(BaseModel):
    situation: str                       # built with the §3.3 template
    event_type: str | None = None        # filter (exact); None = any
    severity_norm: float | None = None   # filter severity in [s-0.3, s+0.3]
    region_hint: str | None = None       # used in keyword part only
    assets: list[str] = []               # tickers to build the distribution for
    horizon: Literal["1d","5d","20d"] = "5d"
    use_abnormal: bool = True
    k: int = 5
    alpha: float = 0.6                   # hybrid weight (1 = pure vector)
    exclude_holdout: bool = False
    as_of: date | None = None
    run_id: str | None = None
```
1. `qvec = embed(situation)`.
2. Run `coll.query.hybrid(query=situation + " " + (region_hint or ""), vector=qvec, alpha=0.6, limit=k*3, filters=..., include_vector=True, return_metadata=MetadataQuery(score=True))`.
3. If fewer than 3 hits come back with filters on, **relax** them: drop the severity filter first, then the type filter. Record each relaxation in `warnings`.
4. **Similarity** = cosine(qvec, object vector), computed by us. The hybrid score is only for ranking and is not a similarity. Keep the top `k` with `similarity ≥ 0.5`.
5. `why_similar` (code-generated) lists matching facets: same type, same region or basin, severity gap, and overlap of the mechanism keywords. P7 (05) turns this into prose.
6. **Distribution** for each asset in `assets`, or a proxy group when an analog lacks that exact ticker (for example `^CNXFMCG` stands in for FMCG names):
   - `w_i = sim_i² / Σ sim²`
   - Use weighted quantiles (`weighted_quantile(values, weights, q)`) for `median`, `p10` and `p90`
   - `n` = number of analogs that have this asset
7. **Conformal interval.** `q̂` comes from `data/conformal_q.json[horizon][event_type]`; the interval is `[median − q̂, median + q̂]` (`conformal_lo`, `conformal_hi`), at the 80% target coverage.
   - `calibrate_conformal.py` runs leave-one-out over the train events: predict each event's outcome as the weighted median of its own top-k analogs, take absolute residuals, and set `q̂ = quantile(residuals, ceil((n+1)·0.8)/n)`.
   - Store `n_calib` with the result. If `n_calib < 8`, add the warning "conformal interval unreliable (n<8)".
8. **Confidence.**
   - `high`: n ≥ 5 and mean similarity ≥ 0.75
   - `medium`: n ≥ 3 and mean similarity ≥ 0.6
   - otherwise `low`
   - `Evidence.confidence` is 0.8, 0.6 or 0.3 respectively, and any filter relaxation subtracts 0.1.

### Example `POST /find_analogs`
```json
{"situation":"cyclone Odisha severity 5 IMD class (1-7). Very severe cyclonic storm expected to make landfall near Puri in 48h with 150 km/h winds and heavy rain. Mechanism: port closures, power outages, kharif crop damage",
 "event_type":"cyclone","severity_norm":0.71,"region_hint":"Odisha","assets":["^NSEI","^CNXFMCG"],"horizon":"5d","k":5}
```
```json
{"evidence":[{"id":"ev_analogs_005","run_id":"run_20261003141502_a91f","tool":"analogs",
 "value":{
  "analogs":[
   {"event_id":"cyclone_fani_2019","title":"Cyclone Fani landfall, Odisha","event_date":"2019-05-03","similarity":0.87,
    "why_similar":["same type: cyclone","same region: Odisha","severity gap 0.14","mechanism overlap: power, crop, port"],
    "outcomes":[{"asset":"^NSEI","ret_5d":-0.012,"abnormal_5d":null},{"asset":"^CNXFMCG","ret_5d":-0.018,"abnormal_5d":-0.006}]},
   {"event_id":"cyclone_phailin_2013","similarity":0.84,"why_similar":["same type","same region","severity gap 0.14"],"outcomes":[]},
   {"event_id":"cyclone_yaas_2021","similarity":0.81,"why_similar":["same type","adjacent region: Odisha/WB"],"outcomes":[]},
   {"event_id":"cyclone_amphan_2020","similarity":0.74,"why_similar":["same type","Bay of Bengal","severity gap 0.29"],"outcomes":[]},
   {"event_id":"cyclone_dana_2024","similarity":0.72,"why_similar":["same type","same region"],"outcomes":[]}],
  "distribution":[
   {"asset":"^CNXFMCG","horizon":"5d","measure":"abnormal","n":5,"median":-0.008,"p10":-0.021,"p90":0.006,
    "conformal_lo":-0.031,"conformal_hi":0.015,"coverage_target":0.8,"n_calib":14},
   {"asset":"^NSEI","horizon":"5d","measure":"raw","n":5,"median":-0.004,"p10":-0.019,"p90":0.011,
    "conformal_lo":-0.024,"conformal_hi":0.016,"coverage_target":0.8,"n_calib":14}],
  "confidence":"high","filters_relaxed":[]},
 "summary":"5 cyclone analogs (mean sim 0.80); FMCG 5d abnormal median −0.8% (p10 −2.1%, p90 +0.6%)",
 "source":"Weaviate HistoricalEvent (40 events, bge-small-en-v1.5) + yfinance outcomes",
 "as_of":"2026-10-03T08:45:00Z","timestamp":"2026-10-03T08:45:05Z","confidence":0.8,"degraded":false,"latency_ms":140,
 "model_version":"analogs_v1"}],"warnings":[]}
```
> `distribution` is a **list**, with one entry per asset. In 01 §6 it is written as a single object. The orchestrator must treat it as a list. See the contract note at the end.

## 7. News indexing (`/news/index`, `/news/search`, `/latency`)
- `POST /news/index {"items":[NewsIn + sentiment_label, sentiment_score, ingested_at]}`:
  - embed in a batch, then `coll.data.insert_many`
  - set `indexed_at = now()` **after the insert returns**
  - dedupe on `news_id` with deterministic UUIDs via `generate_uuid5(news_id)`
- Two latencies are recorded per item. Store both in a ring buffer of 1,000:
  - `pipeline_ms = indexed_at − ingested_at`: embed plus insert. **This is the "sub-second indexing" number.**
  - `end_to_end_s = indexed_at − published_at`: includes the publisher's and feed's delay. Report it honestly and separately.
- `GET /latency` returns `{"pipeline_ms":{"p50":..,"p95":..,"n":..},"end_to_end_s":{"p50":..,"p95":..}}`.
- `POST /news/search {"query","tickers":[],"since_hours":48,"k":10}` runs a hybrid search (alpha 0.5) with filters on tickers and `published_at`.

Example `/news/index` response:
```json
{"evidence":[{"id":"ev_news_014","tool":"news","value":{"indexed":32,"skipped_duplicates":4,
  "pipeline_ms":{"p50":180,"p95":410,"n":32},"end_to_end_s":{"p50":420,"p95":1310}},
  "summary":"Indexed 32 headlines, p95 embed+insert 410 ms","source":"vectordb NewsItem","as_of":"2026-10-03T08:44:59Z",
  "timestamp":"2026-10-03T08:45:00Z","confidence":1.0,"degraded":false}],"warnings":[]}
```

## 8. Build prompt (paste into a coding LLM)
```
Build a FastAPI service "vectordb" (port 8104) using copilot_common (Evidence, ToolResult,
create_service_app, mock_or, EvidenceCounter, settings) and weaviate-client v4.

Step 1. requirements.txt: fastapi uvicorn weaviate-client>=4.9 sentence-transformers yfinance pandas numpy pydantic.
Step 2. vectordb/client.py: get_client() singleton using weaviate.connect_to_local(host=settings.WEAVIATE_HOST,
        port=8080, grpc_port=50051); close on app shutdown; weaviate_ready() -> "ok"/"down" for /health deps.
Step 3. vectordb/embed.py: class Embedder (SentenceTransformer("BAAI/bge-small-en-v1.5"), device cuda if available
        else cpu, encode(texts, batch_size=64, normalize_embeddings=True) -> np.ndarray[n,384]).
        event_text(e: dict) -> str using EXACT template:
        "{event_type} {region} severity {severity_value} {severity_unit}. {description} Mechanism: {mechanism}"
        news_text(n) -> f"{title}. {summary}".
Step 4. vectordb/schema.py: create_collections(reset: bool) creating HistoricalEvent and NewsItem with the properties
        in spec §3, self-provided vectors (Configure.Vectors.self_provided with HNSW cosine; if the installed client
        lacks it, fall back to vectorizer_config=Configure.Vectorizer.none() and vector_index_config cosine).
Step 5. scripts/build_events.py: read data/events_seed.csv (columns: event_id,title,event_type,region,country,
        start_date,end_date,event_date,severity_value,severity_unit,description,mechanism,affected_assets(|-sep),
        tickers(|-sep),split,source,source_url); compute outcomes per spec §5; severity_norm per §3.3;
        write data/historical_events.json. Log skipped tickers.
Step 6. scripts/seed_weaviate.py: load events.json, embed event_text, insert_many with uuid=generate_uuid5(event_id),
        store outcomes_json and embedding_text. Idempotent. Print count.
Step 7. vectordb/stats.py: weighted_quantile(values, weights, q); similarity_weights(sims) = sims²/Σ;
        confidence_label(n, mean_sim); conformal interval loader.
        scripts/calibrate_conformal.py: leave-one-out on split=="train" per spec §6.7 → data/conformal_q.json
        {"5d":{"cyclone":{"q":0.012,"n_calib":10}, ..., "_all":{...}}, "1d":{...}, "20d":{...}}.
Step 8. vectordb/analogs.py: async find_analogs(req: AnalogReq) -> (value: dict, warnings: list[str]) per spec §6,
        including filter relaxation, own cosine similarity from returned vectors, why_similar facets,
        distribution list per asset (abnormal if available and use_abnormal else raw), conformal bounds,
        confidence. Filters: event_type equal, severity_norm range, split=="train" if exclude_holdout,
        event_date < as_of if as_of.
Step 9. vectordb/news.py: index_news(items) and search_news(...) per spec §7 with latency ring buffer.
Step 10. main.py: POST /find_analogs (tool "analogs"), POST /news/index (tool "news"), POST /news/search (tool "news"),
        POST /embed {"texts":[...]} -> {"vectors":[[...]]}, GET /latency. All tool endpoints return ToolResult; mock_or wrap;
        if Weaviate is down return degraded evidence using an in-memory numpy fallback over data/historical_events.json
        (brute-force cosine) — so analogs still work without Docker.
Step 11. tests: weighted_quantile against known values; template exact string; holdout filter never returns holdout
        events; as_of filter; numpy fallback returns same top-1 as Weaviate on a 5-event toy set; MOCK=1 fixture.
Output every file completely.
[PASTE §2–§7 OF 08_VECTOR_DB.md AND §5–7 OF 01_CONTRACTS.md]
```

## 9. Mock mode
With `MOCK=1`, `/find_analogs` returns `fixtures/vectordb/find_analogs.json` (the §6 example), `/news/index` returns the §7 example, and nothing is loaded. If Weaviate is down while `MOCK=0`, the service falls back to brute-force cosine in numpy over `events.json` (`degraded_reason="weaviate_down_numpy_fallback"`, confidence −0.1).

## 10. Acceptance checklist
- [ ] `seed_weaviate.py` inserts at least 35 events. `build_events.py` logs every skipped ticker
- [ ] A query for "Category 4 hurricane Gulf of Mexico refinery coast" returns hurricanes in the top 3, with Harvey, Laura or Ida in the top 3
- [ ] A query for "very severe cyclone Odisha" returns Fani, Phailin or Yaas in the top 3
- [ ] `exclude_holdout=true` never returns the 8 holdout IDs (there is a test for this)
- [ ] `/find_analogs` p95 is under 300 ms on L2
- [ ] News index `pipeline_ms` p95 is under 1,000 ms for a batch of 32, and is shown on the latency panel
- [ ] With Weaviate stopped, `/find_analogs` still answers through the numpy fallback

## 11. Integration hooks
- **Orchestrator `analog_agent` (04).** Builds `situation` from the weather or macro evidence plus the Intent (event_type, region), using the §3.3 template. It passes `assets` = portfolio tickers plus sector index proxies, then calls `/find_analogs`. The top 3 `event_date`s feed `quant /event_study` (06).
- **P7 analog explainer (05).** Input is `value.analogs[*].why_similar` plus `distribution`. It must report ranges and never a single number.
- **Ingestion (10).** After sentiment, calls `/news/index`. The sentiment agent can use `/news/search` instead of RSS for faster recall.
- **Backtest (12).** Always passes `exclude_holdout=true` and `as_of=event_date−1`.
- **Terminal (13).** The analogs table, the distribution box plot (p10, median, p90 plus the conformal band) and the latency panel (`GET /latency`).

## 12. Contract note (for 01)
- `analogs.value.distribution` is a **list** of per-asset objects, each with `measure`, `coverage_target` and `n_calib`.
- `analogs.value.filters_relaxed` is added.

## 13. Sources
- Weaviate hybrid search: https://docs.weaviate.io/weaviate/search/hybrid
- Weaviate filters: https://docs.weaviate.io/weaviate/search/filters
- Weaviate Python v4: https://docs.weaviate.io/weaviate/client-libraries/python
- bge-small-en-v1.5: https://huggingface.co/BAAI/bge-small-en-v1.5
- NOAA HURDAT2: https://www.nhc.noaa.gov/data/#hurdat
- IBTrACS: https://www.ncei.noaa.gov/products/international-best-track-archive
- IMD cyclone reports: https://rsmcnewdelhi.imd.gov.in/
- Split conformal prediction (Angelopoulos & Bates, "A Gentle Introduction to Conformal Prediction", 2021)
