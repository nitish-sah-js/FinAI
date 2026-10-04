# 10 — Data Ingestion Service (prices, news, weather, macro, announcements)

| | |
|---|---|
| **Owner / Laptop** | Data person · **L3 "Edge & UI"** · port **8201** · folder `services/ingestion` |
| **Depends on** | [01_CONTRACTS.md](01_CONTRACTS.md) (`copilot_common`). It pushes to [07 Sentiment](07_SENTIMENT_SERVICE.md) (`/sentiment/score`) and [08 Vector DB](08_VECTOR_DB.md) (`/news/index`), and is polled by [11 Monitor](11_MONITOR_ALERTS.md) |
| **Provides** | Tools `prices`, `news`, `weather`, `macro` (plus corporate announcements as `news` items). Everything is cached and replayable, and every endpoint supports `as_of` (time machine) |
| **Used by** | Orchestrator agents (04): `weather_agent`, `macro_agent`, `sentiment_agent`, `exposure_agent`. Quant (06) for price history. Backtest (12) for pre-event data |

**Design rule:** this is the **only** module that talks to external data APIs. Every other module receives data from it (or from cache) as `Evidence`. That gives the whole system one place for caching, rate limits, replay and chaos.

---

## 1. Folder layout

```
services/ingestion/
├── app.py                    # FastAPI app via copilot_common.create_service_app("ingestion")
├── config/
│   ├── regions.json          # region_id → lat/lon/state/crops (see §5)
│   ├── rss_feeds.json        # feed list (see §4.2)
│   ├── tickers.json          # universe: NSE names + global (CL=F, NG=F, BZ=F, ^NSEI, INR=X)
│   ├── policy_rates.json     # RBI repo history (manual, see §4.6)
│   └── cpi_india.csv         # MoSPI CPI YoY monthly (manual, see §4.6)
├── sources/
│   ├── prices_yf.py          # yfinance wrapper
│   ├── rss.py                # feedparser + dedupe
│   ├── gdelt.py              # GDELT DOC 2.0 API
│   ├── openmeteo.py          # forecast + archive + historical-forecast
│   ├── nasa_power.py         # NASA POWER daily point
│   ├── storms.py             # NHC CurrentStorms.json + IMD/manual storms.json
│   ├── announcements.py      # NSE/BSE corporate announcements (best effort)
│   └── macro.py              # FRED csv, yfinance FX/crude, RBI/MoSPI static
├── features/
│   ├── weather_features.py   # rain anomaly, heat index, soil moisture z
│   └── macro_features.py     # 5d changes, bps
├── store.py                  # parquet/JSON store + as_of filtering
├── worker.py                 # background loop: news → sentiment → vectordb, feed ring buffer
├── fixtures/                 # canned ToolResults for MOCK=1 (copy to copilot_common/fixtures/ingestion)
└── tests/
```

Dependencies: `yfinance pandas pyarrow feedparser httpx python-dateutil rapidfuzz` plus `copilot_common`.

---

## 2. Endpoints (all `POST`, all return `ToolResult`)

| Endpoint | Request body | Evidence `tool` | Cache TTL |
|---|---|---|---|
| `/prices` | `{"tickers":["RELIANCE.NS"],"period":"1y","interval":"1d","as_of":null}` | `prices` (one Evidence per ticker) | 15 min intraday, 12 h daily |
| `/news` | `{"query":"cyclone odisha","tickers":[],"since_hours":48,"limit":30,"as_of":null}` | `news` | 5 min |
| `/weather/features` | `{"region_id":"OD-Puri","horizon_days":5,"as_of":null}` or `{"lat":..,"lon":..}` | `weather` | 30 min |
| `/macro/features` | `{"as_of":null}` | `macro` | 6 h |
| `/announcements` | `{"tickers":["ITC.NS"],"since_hours":72}` | `news` (items carry `category:"corporate_announcement"`) | 15 min |
| `GET /feed/since?ts=<iso>` | — | raw list of new news items plus price ticks (used by 11) | none |
| `GET /regions` | — | the contents of `regions.json` (used by 13 for map/dropdowns) | — |

Every request may also include `"chaos": ChaosFlags` and the header `X-Run-Id`.

---

## 3. Cache, replay and time machine (applies to every source)

- **Raw cache:** `data/cache/ingestion/<source>/<sha256(params)>.json` (or `.parquet` for prices), through `copilot_common.cache.cached`.
- **`CACHE_MODE=replay`:** no network at all; a cache miss returns degraded Evidence (`degraded_reason="cache_miss"`). This is how the team runs the full pipeline dozens of times an hour.
- **Pre-warm:** `python -m services.ingestion.prewarm` fetches everything for the demo universe and regions once. Run it the night before the demo and commit nothing from `data/cache` (it's gitignored). Copy the folder to L1 for single-laptop mode.
- **`as_of` (time machine, used by the backtest in 12):**
  - prices: keep rows with `date <= as_of`.
  - news: keep items with `published_at <= as_of` (GDELT supports `startdatetime/enddatetime`).
  - weather: if `as_of` is in the past, use the **Open-Meteo Historical Forecast API** (archived model forecasts, so there is no look-ahead) when available. Otherwise use the ERA5 archive and set `value.lookahead_risk=true`, and multiply confidence by 0.8.
  - macro: the last value with `date <= as_of`.
  - Evidence `as_of` = the timestamp of the latest data point used, and `freshness_s` = `timestamp − as_of`.

---

## 4. Sources (verified Oct 2026; re-check URLs on day 0)

### 4.1 Prices: yfinance
- NSE tickers use the `.NS` suffix (`RELIANCE.NS`, `ITC.NS`, `HINDUNILVR.NS`, `ONGC.NS`, `IOC.NS`, `BPCL.NS`, `UPL.NS`, `TATACONSUM.NS`, `ADANIPORTS.NS`). Indices are `^NSEI` (NIFTY 50) and `^NSEBANK`. Global series: `CL=F` (WTI), `BZ=F` (Brent), `NG=F` (Henry Hub gas), `INR=X` (USD/INR), `^TNX` (US 10y ×10).
- Use `yf.download(tickers, period=..., interval=..., auto_adjust=True, group_by="ticker", threads=True)` for **batch** download. Never loop single calls.
- Store at `data/cache/ingestion/prices/<ticker>_<interval>.parquet`. Incremental refresh appends only the new rows.

### 4.2 News: RSS + GDELT
`config/rss_feeds.json`:
```json
[
  {"source":"Economic Times Markets","url":"https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"},
  {"source":"Mint Markets","url":"https://www.livemint.com/rss/markets"},
  {"source":"Business Standard Markets","url":"https://www.business-standard.com/rss/markets-106.rss"},
  {"source":"Moneycontrol Latest","url":"https://www.moneycontrol.com/rss/latestnews.xml"},
  {"source":"Moneycontrol Markets","url":"https://www.moneycontrol.com/rss/marketreports.xml"}
]
```
(The Moneycontrol paths are not verified, so open each URL in a browser on day 0 and drop any that are dead.)

- Parse with `feedparser`. The `news_id` is `sha1(normalized_url)[:12]`. Dedupe near-identical titles with `rapidfuzz.fuzz.token_set_ratio > 90`.
- **Ticker tagging:** match `tickers.json` aliases (for example `"ITC.NS": ["ITC", "ITC Ltd"]`) in the title and summary with word-boundary regex. Keep it simple and deterministic.
- **GDELT DOC 2.0** (free, no key) handles event-specific queries:
  `https://api.gdeltproject.org/api/v2/doc/doc?query=<q> sourcecountry:IN&mode=artlist&format=json&maxrecords=50&timespan=48h`
  For `as_of`, use `startdatetime=YYYYMMDDHHMMSS&enddatetime=...` instead of `timespan`. Use `sourcecountry:US` for Gulf hurricane queries.

### 4.3 Weather: Open-Meteo (free, no key, non-commercial)
- **Forecast:** `https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..&daily=precipitation_sum,temperature_2m_max,relative_humidity_2m_mean,wind_speed_10m_max&hourly=soil_moisture_0_to_1cm,soil_moisture_1_to_3cm,soil_moisture_3_to_9cm&forecast_days=7&timezone=Asia/Kolkata`
- **Archive (ERA5 / ERA5-Land, since 1940):** `https://archive-api.open-meteo.com/v1/archive?latitude=..&longitude=..&start_date=..&end_date=..&daily=precipitation_sum,temperature_2m_max&hourly=soil_moisture_0_to_7cm`
- **Historical Forecast API** (for `as_of` without look-ahead): `https://historical-forecast-api.open-meteo.com/v1/forecast?...&start_date=..&end_date=..`
- Variable names differ between endpoints (the forecast uses `0_to_1cm`, `1_to_3cm`, `3_to_9cm`, while ERA5 uses `0_to_7cm`). Check the docs pages and map them in one place (`openmeteo.py`).

### 4.4 NASA POWER (free, no key), the agri/rain cross-check
> **2026-10-04: not implemented.** `sources/nasa_power.py` was written but never imported, and the agri model (09) was trained on Open-Meteo ERA5 weather instead. The dead module was removed; wire NASA POWER in only together with a retrained agri model.

`https://power.larc.nasa.gov/api/temporal/daily/point?parameters=PRECTOTCORR,T2M,T2M_MAX,RH2M,GWETROOT&community=AG&latitude=..&longitude=..&start=YYYYMMDD&end=YYYYMMDD&format=JSON`
`GWETROOT` is root-zone soil wetness (0–1). The agri module (09) also uses it for training features, so expose `nasa_power.fetch(lat, lon, start, end) -> pd.DataFrame` as an importable function.

### 4.5 Storms
- **Atlantic/Gulf (Hurricane demo):** NOAA NHC `https://www.nhc.noaa.gov/CurrentStorms.json` (active storms: name, classification, intensity in knots, lat/lon, movement). Convert knots to a Saffir-Simpson category in code.
- **North Indian Ocean (cyclone demo):** IMD RSMC New Delhi bulletins are PDF and HTML only (mausam.imd.gov.in), with no stable JSON API. Use a **manual** `data/storms.json`, kept by hand during the demo window or seeded with a past storm for rehearsal:
  ```json
  [{"name":"DEMO-ODISHA","basin":"NIO","category":"VSCS","imd_class":"Very Severe Cyclonic Storm",
    "lat":18.9,"lon":87.2,"max_wind_kt":75,"track_toward":["OD-Puri","AP-Visakhapatnam"],
    "landfall_eta":"2026-10-05T18:00:00Z","source":"IMD RSMC bulletin (manual entry)"}]
  ```
  Be honest in the pitch that this is entered by hand from IMD bulletins.
- A storm is attached to a region if its great-circle distance from the region is under 500 km or the region appears in `track_toward`.

### 4.6 Macro
| Feature | Source | Key needed |
|---|---|---|
| `usd_inr`, `usd_inr_chg_5d` | yfinance `INR=X` | no |
| `brent`, `brent_chg_5d` | yfinance `BZ=F` (EIA API as cross-check, free key) | no / EIA free key |
| `us10y`, `us10y_chg_5d` | FRED CSV `https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10` (no key) | no |
| `repo_rate`, `repo_change_bps` | `config/policy_rates.json`, updated by hand from RBI press releases or DBIE | no |
| `cpi_yoy` | `config/cpi_india.csv`, monthly from MoSPI press releases (download the CSV once) | no |

RBI and MoSPI data comes from manual files on purpose. Their portals don't offer a stable free JSON API, and these figures change monthly at most. Write the source honestly in `Evidence.source`, for example `"RBI policy press releases (manual file, updated 2026-10-01)"`.

`policy_rates.json`:
```json
[{"date":"2025-06-06","repo_rate":5.50},{"date":"2025-12-05","repo_rate":5.25}]
```
(These are example values. Fill in the real history from RBI before the demo.)

### 4.7 Corporate announcements (best effort, brittle)
- The NSE JSON (`https://www.nseindia.com/api/corporate-announcements?index=equities&symbol=ITC`) needs a browser-like session: first GET `https://www.nseindia.com` to obtain cookies, then call the API with `User-Agent` and `Referer` headers. It may block you. Wrap it in try/except, cache aggressively and fall back to fixtures.
- Each announcement becomes a `news` item with `category: "corporate_announcement"`, `source: "NSE"`.

---

## 5. Regions (`config/regions.json`)

The **region_id format `<STATE>-<District>` must match the agri module (09) and `region_exposure.json`.**

```json
[
  {"region_id":"OD-Puri","name":"Puri (Odisha coast)","country":"IN","lat":19.81,"lon":85.83,"crops":["paddy"],"coastal":true},
  {"region_id":"AP-Visakhapatnam","name":"Visakhapatnam","country":"IN","lat":17.69,"lon":83.22,"crops":["paddy"],"coastal":true},
  {"region_id":"GJ-Kutch","name":"Kutch (Gujarat)","country":"IN","lat":23.25,"lon":69.67,"crops":["cotton","groundnut"],"coastal":true},
  {"region_id":"MH-Mumbai","name":"Mumbai","country":"IN","lat":19.08,"lon":72.88,"crops":[],"coastal":true},
  {"region_id":"MH-Yavatmal","name":"Yavatmal (Vidarbha)","country":"IN","lat":20.39,"lon":78.12,"crops":["cotton","soybean"]},
  {"region_id":"MH-Nagpur","name":"Nagpur (Vidarbha)","country":"IN","lat":21.15,"lon":79.09,"crops":["cotton","orange"]},
  {"region_id":"MP-Indore","name":"Indore (Malwa)","country":"IN","lat":22.72,"lon":75.86,"crops":["soybean","wheat"]},
  {"region_id":"PB-Ludhiana","name":"Ludhiana (Punjab)","country":"IN","lat":30.90,"lon":75.85,"crops":["wheat","paddy"]},
  {"region_id":"AP-Guntur","name":"Guntur","country":"IN","lat":16.31,"lon":80.44,"crops":["chilli","cotton"]},
  {"region_id":"CG-Raipur","name":"Raipur (Chhattisgarh)","country":"IN","lat":21.25,"lon":81.63,"crops":["paddy"]},
  {"region_id":"TN-Chennai","name":"Chennai","country":"IN","lat":13.08,"lon":80.27,"crops":[],"coastal":true},
  {"region_id":"WB-Kolkata","name":"Kolkata","country":"IN","lat":22.57,"lon":88.36,"crops":["jute","paddy"],"coastal":true},
  {"region_id":"US-LA-PortFourchon","name":"Port Fourchon, Louisiana","country":"US","lat":29.11,"lon":-90.20,"crops":[],"coastal":true},
  {"region_id":"US-TX-Houston","name":"Houston, Texas","country":"US","lat":29.76,"lon":-95.37,"crops":[],"coastal":true},
  {"region_id":"GULF-Central","name":"Central Gulf of Mexico (offshore)","country":"US","lat":27.5,"lon":-90.0,"crops":[],"coastal":true}
]
```

---

## 6. Feature formulas (pure functions in `features/`, all unit-tested)

```python
# weather_features.py
def rain_anomaly_pct(forecast_mm: float, clim_mm: float) -> float:
    """forecast_mm = sum of daily precipitation over the next horizon_days.
    clim_mm = mean of the SAME calendar window (day-of-year range) over the climatology years
    (default 2001-2025 from the ERA5 archive, cached once per region in data/cache/ingestion/clim/).
    Returns 100 * (forecast_mm - clim_mm) / max(clim_mm, 1.0), rounded to 1 decimal."""

def heat_index_c(t_c: float, rh: float) -> float:
    """NOAA Rothfusz regression (convert C→F, apply, convert back). If HI_F < 80, use the simple Steadman formula."""

def soil_moisture_z(current: float, clim_mean: float, clim_std: float) -> float:
    return (current - clim_mean) / max(clim_std, 1e-6)

def saffir_simpson(max_wind_kt: float) -> int:  # 0 = below hurricane strength
    # 64-82 →1, 83-95 →2, 96-112 →3, 113-136 →4, >=137 →5

def weather_confidence(source_ok: bool, horizon_days: int, lookahead_risk: bool) -> float:
    c = 0.85 if source_ok else 0.4
    c -= 0.05 * max(0, horizon_days - 3)          # forecasts decay with horizon
    if lookahead_risk: c *= 0.8
    return round(max(0.1, min(c, 0.95)), 2)

# macro_features.py
def pct_change_n(series: pd.Series, n: int = 5) -> float     # (last / value n trading days ago) - 1
def bps_change(new_rate: float, old_rate: float) -> int      # round((new - old) * 100)
```

Threshold list `weather.alerts` (simple and deterministic, also used by 11):
- `"heavy_rain"` if the forecast maximum daily precipitation is over 115.6 mm (IMD "very heavy" threshold) or `rain_anomaly_pct > 100`
- `"rain_deficit"` if `rain_anomaly_pct < -40` during June–September
- `"heatwave"` if `max_temp_c >= 45` (plains) or `heat_index_c >= 41`
- `"cyclone"` / `"hurricane"` if a storm is attached (§4.5)

---

## 7. Background worker (`worker.py`, started on app startup)

```
every NEWS_POLL_S (default 120 s):
  1. fetch all RSS feeds (and GDELT for the active demo query terms), dedupe against seen news_ids (SQLite data/ingest_seen.db)
  2. new items → POST {SENTIMENT_URL}/sentiment/score  {"items":[{news_id,title,summary,tickers}]}
  3. merge sentiment fields → POST {VECTOR_URL}/news/index {"items":[NewsItem...]}   (08 measures indexed_at - ingested_at)
  4. append to an in-memory ring buffer (last 2,000 items) served by GET /feed/since
every PRICE_POLL_S (default 60 s during market hours 09:15–15:30 IST, else 900 s):
  5. batch yf.download(universe, period="5d", interval="5m") → ring buffer price ticks
```
- If sentiment or vectordb is down, keep the items in a retry queue (max 500) and mark `/health` as `degraded` with `deps.sentiment="down"`. **Never block ingestion** on downstream services.
- `ingested_at` is stamped when the item is first parsed. The vector DB stamps `indexed_at`.
- `REPLAY=1` (or `CACHE_MODE=replay`) makes the worker replay `data/cache/ingestion/news_replay.jsonl` at ×10 speed, so the monitor and pet demo works offline.

---

## 8. Example outputs

### `/weather/features` (cyclone demo)
Request:
```json
{"region_id":"OD-Puri","horizon_days":5}
```
Response:
```json
{
  "evidence": [{
    "id": "ev_weather_001",
    "run_id": "run_20261003141502_a91f",
    "tool": "weather",
    "value": {
      "region": "OD-Puri", "lat": 19.81, "lon": 85.83,
      "rain_anomaly_pct": 184.0,
      "heat_index_c": 33.1, "max_temp_c": 31.4,
      "soil_moisture_0_7cm": 0.41, "soil_moisture_z": 1.9,
      "storm": {"name": "DEMO-ODISHA", "category": "VSCS", "basin": "NIO", "track_toward": ["OD-Puri","AP-Visakhapatnam"], "distance_km": 310},
      "alerts": ["cyclone", "heavy_rain"],
      "horizon_days": 5,
      "daily": [{"date":"2026-10-03","precip_mm":12.1,"tmax_c":31.4},{"date":"2026-10-04","precip_mm":88.0,"tmax_c":29.9}],
      "lookahead_risk": false
    },
    "summary": "Puri: 5-day rain +184% vs 2001-2025 normal; VSCS 310 km away tracking toward coast",
    "source": "Open-Meteo forecast + ERA5 climatology; IMD RSMC bulletin (manual)",
    "source_url": "https://api.open-meteo.com/v1/forecast",
    "as_of": "2026-10-03T08:00:00Z",
    "timestamp": "2026-10-03T08:45:04Z",
    "freshness_s": 2704,
    "confidence": 0.75,
    "degraded": false,
    "latency_ms": 840
  }],
  "warnings": []
}
```

### `/macro/features`
```json
{"evidence":[{"id":"ev_macro_001","tool":"macro","value":{
  "repo_rate":5.25,"repo_change_bps":-25,"cpi_yoy":3.1,
  "usd_inr":88.4,"usd_inr_chg_5d":0.006,
  "brent":71.2,"brent_chg_5d":0.043,
  "us10y":4.12,"us10y_chg_5d":-0.0005,
  "series_dates":{"repo_rate":"2025-12-05","cpi_yoy":"2026-08-31","usd_inr":"2026-10-02"}},
  "summary":"Brent +4.3% in 5d, INR flat, repo 5.25% after last -25bp",
  "source":"yfinance (INR=X, BZ=F); FRED DGS10; RBI/MoSPI manual files",
  "as_of":"2026-10-02T15:30:00Z","timestamp":"2026-10-03T08:45:03Z","freshness_s":62100,
  "confidence":0.8,"degraded":false}],"warnings":[]}
```
(The numbers are illustrative. Real values come from the sources.)

### `/news`
```json
{"evidence":[{"id":"ev_news_001","tool":"news","value":{"items":[
  {"news_id":"a3f9c01b22de","title":"Cyclone alert: IMD warns of very severe storm off Odisha coast","summary":"...","source":"Economic Times Markets","url":"https://economictimes.indiatimes.com/...","published_at":"2026-10-03T06:10:00Z","tickers":[],"category":"news"},
  {"news_id":"7bc2e91f0a11","title":"Paradip port suspends operations ahead of cyclone","summary":"...","source":"GDELT:business-standard.com","url":"https://...","published_at":"2026-10-03T07:02:00Z","tickers":["ADANIPORTS.NS"],"category":"news"}]},
  "summary":"2 items, 48h, query 'cyclone odisha'","source":"RSS (ET, Mint, BS, Moneycontrol) + GDELT DOC 2.0",
  "as_of":"2026-10-03T07:02:00Z","timestamp":"2026-10-03T08:45:03Z","confidence":null,"degraded":false}],"warnings":[]}
```

### `/prices` (one ticker shown)
```json
{"evidence":[{"id":"ev_prices_001","tool":"prices","value":{"ticker":"ONGC.NS","interval":"1d",
  "rows":[{"date":"2026-09-30","open":242.1,"high":245.0,"low":240.8,"close":244.2,"volume":11823400},
          {"date":"2026-10-01","open":244.5,"high":249.9,"low":243.7,"close":249.1,"volume":15322100}]},
  "source":"yfinance","as_of":"2026-10-01T10:00:00Z","timestamp":"2026-10-03T08:45:02Z","confidence":0.95,"degraded":false}],"warnings":[]}
```

### Degraded example (chaos `weather_down`)
```json
{"evidence":[{"id":"ev_weather_001","tool":"weather","value":{"region":"OD-Puri","rain_anomaly_pct":171.0,"alerts":["cyclone"],"...":"last cached"},
  "summary":"Open-Meteo unavailable; using cached value from 6h ago","source":"cache (Open-Meteo)",
  "as_of":"2026-10-03T02:00:00Z","timestamp":"2026-10-03T08:45:04Z","freshness_s":24304,
  "confidence":0.38,"degraded":true,"degraded_reason":"chaos"}],"warnings":["weather_down chaos flag set"]}
```

---

## 9. Mock mode
`MOCK=1` returns `fixtures/ingestion/<endpoint>.json` (the examples above) with `degraded_reason="mock"`. The worker in mock mode replays `news_replay.jsonl` instead of polling RSS, so teammates on L1 and L2 can develop with no internet.

---

## 10. Build prompt (copy-paste into a coding LLM)

```
You are building the data-ingestion microservice of a multi-service Python project.
Use FastAPI, httpx (async), pandas, pyarrow, yfinance, feedparser, rapidfuzz.
A shared package `copilot_common` already exists (I paste its models.py, cache.py and service_base.py
signatures below). Use its Evidence, ToolResult, ChaosFlags, create_service_app, cached(), EvidenceCounter.
NEVER invent new response shapes: every tool endpoint returns ToolResult.

Step 1. Create services/ingestion/app.py: app = create_service_app("ingestion", deps_check=check_deps).
        check_deps pings Open-Meteo, yfinance (cheap call), SENTIMENT_URL/health, VECTOR_URL/health.
Step 2. config/: write regions.json, rss_feeds.json, tickers.json (with "aliases" per ticker),
        policy_rates.json, cpi_india.csv exactly as in the spec.
Step 3. store.py:
        - save_prices(ticker, interval, df), load_prices(ticker, interval, as_of=None) -> df  (parquet,
          incremental append, filter date <= as_of)
        - filter_as_of(items, as_of, key="published_at")
Step 4. sources/prices_yf.py: async fetch_prices(tickers, period, interval) using yf.download in
        asyncio.to_thread, batch, auto_adjust=True; returns dict[ticker, DataFrame].
Step 5. sources/rss.py: async fetch_feeds(feeds) -> list[dict] with news_id = sha1(normalized url)[:12],
        dedupe by id and rapidfuzz token_set_ratio>90 on titles; tag_tickers(text, tickers_json) by word-boundary regex.
        sources/gdelt.py: async search(query, country="IN", timespan="48h" | start/end for as_of, maxrecords=50).
Step 6. sources/openmeteo.py: forecast(lat,lon,days), archive(lat,lon,start,end), historical_forecast(lat,lon,start,end),
        climatology(lat,lon,doy_start,doy_end, years=2001-2025) cached forever per region.
        Map soil-moisture variable names per endpoint in ONE dict.
        sources/nasa_power.py: fetch(lat,lon,start,end, params=PRECTOTCORR,T2M,T2M_MAX,RH2M,GWETROOT, community=AG) -> DataFrame.
        sources/storms.py: nhc_current() from https://www.nhc.noaa.gov/CurrentStorms.json, manual_storms() from data/storms.json,
        attach_storm(region, storms) using haversine < 500 km or region in track_toward.
Step 7. sources/macro.py: fx/brent via yfinance INR=X, BZ=F; us10y via FRED csv DGS10 (no key);
        repo and cpi from the manual config files. sources/announcements.py: NSE session-cookie fetch, wrapped in try/except.
Step 8. features/weather_features.py and macro_features.py: implement EXACTLY the pure functions in spec section 6,
        including the alerts threshold list. Write pytest tests for each with hand-computed values.
Step 9. Endpoints /prices /news /weather/features /macro/features /announcements (POST) + GET /feed/since, GET /regions.
        Each: honour MOCK, CACHE_MODE (via cached()), as_of (spec section 3), chaos flags (weather_down → serve
        last cache with degraded=True, confidence*0.5, degraded_reason="chaos"), X-Run-Id for evidence ids.
        Set Evidence.as_of = latest data timestamp, freshness_s, confidence (weather_confidence), latency_ms, summary (code-written one-liner).
Step 10. worker.py: asyncio background tasks started in lifespan: news loop (fetch→/sentiment/score→/news/index→ring buffer)
        and price loop (market-hours aware, IST). Retry queue max 500. REPLAY mode reads news_replay.jsonl at x10 speed.
Step 11. prewarm.py: CLI that fetches and caches all regions (forecast + climatology), the ticker universe (2y daily),
        macro, and 7 days of news, then prints a summary table.
Step 12. fixtures/: write the example JSON outputs from the spec section 8 as fixture files.
Step 13. tests/: feature-formula tests, as_of filtering test (no row after as_of ever returned),
        replay test (CACHE_MODE=replay with an empty cache → degraded evidence, HTTP 200, no network).
Deliver complete files, no TODOs.
[PASTE 01_CONTRACTS.md §4–7 AND THIS FILE §2–8]
```

---

## 11. Acceptance checklist
- [ ] `POST /weather/features {"region_id":"OD-Puri"}` returns valid `ToolResult`, `Evidence.tool=="weather"`, and all required `value` keys from 01 §6
- [ ] `POST /macro/features` returns every macro key from 01 §6
- [ ] `/prices` for 10 tickers takes under 5 s cold and under 100 ms warm (cache)
- [ ] `CACHE_MODE=replay` with the network unplugged: the full demo query still gets weather, macro, news and prices evidence
- [ ] `as_of="2021-08-25"` never returns data dated after that day (tested)
- [ ] Chaos `weather_down` returns degraded Evidence with HTTP 200
- [ ] The worker pushes new news to sentiment and vectordb, and a downstream outage only degrades `/health`
- [ ] `prewarm` completes for all 15 regions

## 12. Integration hooks
- **04 Orchestrator** calls `INGEST_URL` endpoints from `weather_agent`, `macro_agent` and `sentiment_agent` (news first, then sentiment), and from `exposure_agent` (prices). The orchestrator passes `as_of` and `chaos` straight through.
- **07 Sentiment** must accept `{"items":[{news_id,title,summary,tickers}]}` at `/sentiment/score`. Align with the 07 owner.
- **08 Vector DB** receives NewsItem fields `title, summary, source, published_at, tickers, sentiment_label, sentiment_score, ingested_at` at `/news/index`.
- **09 Agri** imports `sources/nasa_power.fetch` and `sources/openmeteo.archive` for training features, and must use the same `region_id`s.
- **11 Monitor** polls `GET /feed/since` every 15 s and calls `/weather/features` for regions linked to holdings.
- **12 Backtest** calls every endpoint with `as_of` = the day before each event.
- **13 Frontend** uses `GET /regions` for the region picker and map.

## 13. Sources
- [Open-Meteo Historical Weather API (ERA5, soil moisture levels)](https://open-meteo.com/en/docs/historical-weather-api) · [Open-Meteo docs](https://open-meteo.com/en/docs)
- [NASA POWER API tutorial](https://power.larc.nasa.gov/docs/tutorials/service-data-request/api/) · [nasapower parameter reference](https://docs.ropensci.org/nasapower/reference/get_power.html)
- [GDELT DOC 2.0 API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/) · [gdeltdoc Python client](https://github.com/alex9smith/gdelt-doc-api)
- [Indian stock RSS feeds list](https://rss.feedspot.com/indian_stocks_and_trading_rss_feeds/) · [news-fetcher (ET/Mint/BS feed URLs)](https://github.com/SunnyYadav16/news-fetcher)
- NOAA NHC CurrentStorms.json (https://www.nhc.noaa.gov/), IMD RSMC New Delhi (https://mausam.imd.gov.in/), FRED (https://fred.stlouisfed.org/series/DGS10). Verify these on day 0.
