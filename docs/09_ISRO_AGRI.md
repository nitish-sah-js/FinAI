# 09 — ISRO Imagery Agri Model & Service (L2 :8103)

| | |
|---|---|
| **Owner / Laptop** | Agri/remote-sensing person · **L2 "Quant & ML"** · port **8103** · folder `services/agri` |
| **Status** | ✅ Built 2026-10-03 (`services/agri`, 38 tests, model `gbm_v2`). 8 districts (GET /regions). LOYO CV: acc 0.605 / F1 0.552 vs persistence 0.624 / 0.553, so it does **not** beat persistence; confidence ×0.8 and `/model_info` says so. No yield model (`yield_anomaly_pct: null`). Latest imagery 2026-01-03, so live answers are degraded `stale_imagery` |
| **Depends on** | `copilot_common` (01). Downloaded NDVI rasters or zonal CSVs (you supply them). District boundaries. Rain and soil moisture from Open-Meteo or NASA POWER (or ingestion `/weather/features`, 10). `data/region_exposure.json` |
| **Provides** | Tool `agri` (`POST /agri_signal`), `POST /agri_signal/batch`, `GET /regions`, `GET /model_info` |
| **Called by** | Orchestrator `agri_agent`, then the gemma3 narrator P5 (04/05). Monitor `agri_stress` alerts (11). Backtest (12) |

> **What this module promises.** Kimi, Groq or Qwen **never see pixels**. They only see the typed JSON below. Crop stress is a **slow signal** (weeks to months), not an intraday one. If you train the model yourself, you must deliver exactly the artifacts in §7.

---

## 1. THE OUTPUT (the contract your model must produce)

`POST /agri_signal` request:
```json
{"region_id":"MH-Yavatmal","date":"2026-09-15","crop_season":"kharif","as_of":null,"run_id":"run_20261003141502_a91f"}
```
Response (`ToolResult`):
```json
{"evidence":[{"id":"ev_agri_004","run_id":"run_20261003141502_a91f","tool":"agri",
 "value":{
   "region_id":"MH-Yavatmal","district":"Yavatmal","state":"Maharashtra","crop_season":"kharif",
   "main_crops":["cotton","soybean","tur"],
   "period_start":"2026-08-29","period_end":"2026-09-13",
   "features":{"ndvi_mean":0.41,"ndvi_max":0.68,"ndvi_std":0.09,"ndvi_anomaly_z":-1.4,"vci":0.28,
               "evi_mean":0.27,"rain_anomaly_pct":-22.0,"soil_moisture_0_7cm":0.18,"soil_moisture_anomaly_z":-1.1,
               "lst_anomaly_c":1.6,"valid_pixel_frac":0.83},
   "stress_class":"stressed",
   "class_probs":{"healthy":0.05,"watch":0.20,"stressed":0.60,"severe":0.15},
   "stress_lead_days":16,
   "yield_anomaly_pct":{"q10":-18.0,"q50":-9.5,"q90":-1.0},
   "trend":{"ndvi_anomaly_z_prev":-0.9,"direction":"worsening"},
   "linked_equities":[{"ticker":"UPL.NS","link":"crop protection demand","sign":"+"},
                      {"ticker":"M&M.NS","link":"rural tractor demand","sign":"-"}],
   "baseline_years":"2016-2025","n_years_baseline":10,
   "model_version":"gbm_v1","data_source":"MODIS MOD13Q1 NDVI 250m 16-day (district zonal mean)",
   "label_source":"rule-based stress_class (VCI + rain deficit), yield: ICRISAT DLD",
   "degraded":false},
 "summary":"Yavatmal kharif: NDVI 1.4σ below normal, VCI 0.28 → stressed (p=0.60); yield q50 −9.5% (q10 −18%, q90 −1%)",
 "source":"agri gbm_v1 on MODIS MOD13Q1 NDVI + Open-Meteo ERA5-Land soil moisture",
 "source_url":"https://lpdaac.usgs.gov/products/mod13q1v061/",
 "as_of":"2026-09-13T00:00:00Z","timestamp":"2026-10-03T08:45:05Z","freshness_s":1672000,
 "confidence":0.62,"degraded":false,"latency_ms":220,"model_version":"gbm_v1"}],
 "warnings":[]}
```
Rules:
- The four `stress_class` values are fixed, as is the key order of `class_probs`: `healthy, watch, stressed, severe`.
- `yield_anomaly_pct` is **percent vs trend**, so −9.5 means 9.5% below trend. If you have no yield model, return `null` and add a warning.
- `data_source` must name the **real** imagery you used: ISRO AWiFS, MODIS or Sentinel-2. Don't call MODIS "ISRO".
- **`confidence`** = `max(class_probs) × min(1, valid_pixel_frac/0.7) × (1.0 if all features are present else 0.7) × (0.5 if degraded)`, clipped to [0, 1].

## 2. Folder layout
```
services/agri/
├── agri/
│   ├── main.py            # create_service_app("agri")
│   ├── regions.py         # load data/regions.json (id, district, state, crops, geometry file, lat/lon centroid)
│   ├── zonal.py           # zonal_stats(raster_path, geometry) → ndvi_mean/max/std, valid_pixel_frac
│   ├── features.py        # build_features(region_id, period) using baselines.parquet + weather
│   ├── weather_feats.py   # rain_anomaly_pct, soil moisture (Open-Meteo archive / NASA POWER), cached
│   ├── predict.py         # load artifacts (§7), predict class + quantiles, confidence
│   └── exposure.py        # region_exposure.json → linked_equities
├── training/
│   ├── 01_download.md     # notes/commands for your chosen source (§3)
│   ├── 02_zonal_stats.py  # rasters → data/agri/zonal.parquet
│   ├── 03_build_table.py  # → data/agri/agri_training.csv + agri_yield_training.csv
│   ├── 04_train.py        # LightGBM + LOYO CV → services/agri/models/gbm_v1/
│   └── 05_report.py       # CV report + plots
├── models/gbm_v1/         # ARTIFACTS (§7)
├── tests/
└── requirements.txt       # fastapi uvicorn rasterio rasterstats geopandas shapely lightgbm scikit-learn pandas pyarrow httpx
data/agri/rasters/<source>/<region_id>/<period_end>.tif   # or one national tile per period
data/agri/boundaries/districts.gpkg
data/regions.json  data/region_exposure.json
```

## 3. What to download (choose one primary source, plus one backup)
Pick **5 to 8 districts** with **at least 8 years** of data (10 or more is better) at a 16-day or monthly cadence for each season.

Suggested districts, as `region_id` = `<state code>-<District>`:

| region_id | Main crops (season) | Why |
|---|---|---|
| MH-Yavatmal | cotton, soybean (kharif) | cotton belt, rain-fed, frequent stress |
| MH-Latur | soybean, tur (kharif) | drought-prone Marathwada |
| MP-Indore | soybean (kharif), wheat (rabi) | soy hub |
| MP-Ujjain | soybean (kharif) | soy |
| GJ-Rajkot | groundnut, cotton (kharif) | edible oil |
| PB-Ludhiana | rice (kharif), wheat (rabi) | irrigated control, low stress |
| KA-Kalaburagi | tur (kharif) | pulses |
| RJ-Jodhpur | bajra (kharif) | arid, extreme anomalies |

| Option | Source | How | Notes |
|---|---|---|---|
| **A (ISRO, preferred for the pitch)** | Bhoonidhi, Resourcesat-2/2A **AWiFS NDVI Time Composite** (100 m, 10°×10° tiles) | Register at bhoonidhi.nrsc.gov.in. Use the web UI or the **STAC API** (spec at `bhoonidhi.nrsc.gov.in/bhoonidhi-api`; email bhoonidhi@nrsc.gov.in to get API access enabled). The community CLI also works: `pip install bhoonidhi-downloader`, then `bhd auth login` (email OTP), `bhd query create <start> <end> --sat <mission:sensor> --minx .. --maxx .. --miny .. --maxy ..` and `bhd query download <slug> --out data/agri/rasters/awifs` | Check in the **first hour** which products and years your account can download. The NDVI composite may not cover 10 years. If it is short, use AWiFS for the current year and MODIS for the baseline, and say so |
| **B (fastest backup)** | MODIS **MOD13Q1 v061** NDVI/EVI, 250 m, 16-day, from 2000 to now | **Google Earth Engine** `MODIS/061/MOD13Q1`. Run `reduceRegions` over the district polygons and **export a CSV of zonal means directly**, which skips raster handling. Or use **NASA AppEEARS** (area request, GeoTIFF or CSV) | NDVI scale factor 0.0001. Filter by `SummaryQA` ≤ 1. This is the easiest path to 20 years of baseline |
| C | Sentinel-2 L2A (10 m), `COPERNICUS/S2_SR_HARMONIZED` on GEE | Monthly median NDVI after cloud masking (SCL), with reduceRegions per district | Only from 2017, so the baseline is short. Use it for detail, not for the baseline |
| Weather features | Open-Meteo Historical API (ERA5 / ERA5-Land): `precipitation_sum`, `soil_moisture_0_to_7cm`, `temperature_2m_max`. No key needed. NASA POWER is the backup. IMD gridded rain via `imdlib` is optional | Use the district centroid lat/lon. Use the area mean over 3–5 points if time allows | |
| LST (optional) | MODIS MOD11A2 (8-day LST) on GEE | Compute `lst_anomaly_c` against the same-period mean | |
| Boundaries | GADM level 2 (India districts), DataMeet `maps` (github.com/datameet/maps), or Bhuvan / Survey of India | Save as `districts.gpkg` in EPSG:4326 and record `region_id` → polygon | **For any map shown in the UI, use the official Survey of India boundary** |
| Yield labels | ICRISAT District Level Database (DLD), or DES crop statistics (aps.dac.gov.in / data.gov.in) | Area, production and yield by district, crop and year | District names change; keep a name-mapping CSV |

GEE snippet for option B (JavaScript Code Editor):
```js
var districts = ee.FeatureCollection('users/<you>/districts_8');           // upload your 8 polygons
var ndvi = ee.ImageCollection('MODIS/061/MOD13Q1').select(['NDVI','EVI','SummaryQA'])
  .filterDate('2010-01-01','2026-10-01');
var stats = ndvi.map(function(img){
  var good = img.select('SummaryQA').lte(1);
  var v = img.select(['NDVI','EVI']).multiply(0.0001).updateMask(good);
  return v.reduceRegions({collection: districts,
      reducer: ee.Reducer.mean().combine(ee.Reducer.max(),'',true).combine(ee.Reducer.stdDev(),'',true)
               .combine(ee.Reducer.count(),'',true), scale: 250})
    .map(function(f){ return f.set('period_end', img.date().advance(15,'day').format('YYYY-MM-dd')); });
}).flatten();
Export.table.toDrive({collection: stats, description: 'modis_zonal', fileFormat: 'CSV'});
```

## 4. Feature engineering (`agri_training.csv`: one row per district per period)
| column | type | definition |
|---|---|---|
| `region_id` | str | `MH-Yavatmal` |
| `district`, `state` | str | |
| `crop_season` | str | `kharif` (Jun–Oct) or `rabi` (Nov–Mar). Periods outside both are `zaid` or `none`; drop them |
| `year` | int | season year. Rabi Nov-2025→Mar-2026 = 2026 |
| `period_end` | date | end of the 16-day or monthly composite |
| `doy_bin` | int | period-of-year index (1–23 for 16-day), used for baselines |
| `ndvi_mean`, `ndvi_max`, `ndvi_std` | float | district zonal statistics over valid pixels (optionally cropland-masked) |
| `evi_mean` | float | optional |
| `valid_pixel_frac` | float | valid / total pixels |
| `ndvi_anomaly_z` | float | `(ndvi_mean − μ_doy) / σ_doy`, where μ and σ come from the **same district, same doy_bin, other years** (leave the current year out) |
| `vci` | float | `(ndvi_mean − min_doy) / (max_doy − min_doy)`, clipped to [0, 1], with min and max over the other years |
| `ndvi_delta` | float | `ndvi_mean − ndvi_mean(previous period)` |
| `rain_30d_mm` | float | precipitation sum over the 30 days to `period_end` |
| `rain_anomaly_pct` | float | `100·(rain_season_to_date − normal)/normal`, where normal is the mean of other years |
| `soil_moisture_0_7cm` | float | ERA5-Land m³/m³, mean over the period |
| `soil_moisture_anomaly_z` | float | z-score vs the same doy_bin in other years |
| `lst_anomaly_c` | float | optional |
| `stress_class_now` | str | rule label at this period (§5) |
| **`target_stress_class`** | str | `stress_class_now` of the **next** period (lead = 1 period, about 16 days). **This is the classifier's target** |

`agri_yield_training.csv` has one row per district per season-year (the yield target lives at season grain):

| column | definition |
|---|---|
| `region_id, crop, crop_season, year` | |
| `decision_period_end` | date up to which features are allowed (e.g. Sep 15 for kharif, Feb 15 for rabi) |
| features | season-to-date aggregates up to the decision date: `ndvi_anom_mean, ndvi_anom_min, vci_min, vci_mean, rain_anomaly_pct, sm_anom_mean, lst_anom_mean` |
| **`yield_anomaly_pct`** | `100·(yield − trend)/trend`. Trend = linear fit of yield on year for that district and crop, **excluding the target year** |

## 5. Labels when you don't have yield (state this openly in the pitch)
Rule-based `stress_class_now`. Evaluate top-down and take the first match:
1. `severe`: `vci < 0.20` **and** `rain_anomaly_pct ≤ −30`
2. `stressed`: `vci < 0.35` **or** (`ndvi_anomaly_z ≤ −1.0` **and** `rain_anomaly_pct ≤ −20`)
3. `watch`: `vci < 0.50` **or** `ndvi_anomaly_z ≤ −0.5`
4. `healthy`: everything else

**Avoid leakage.** Predicting today's rule label from the same features it was computed from is not ML; it just reproduces the rule. That is why the classifier target is the **next period's** class (`target_stress_class`). The model has to forecast it from current levels, trends (`ndvi_delta`) and the soil and rain state. Always report results against two baselines: "persistence" (next = now) and "always healthy". If the model doesn't beat persistence, say so.

## 6. Training (`training/04_train.py`)
- **Classifier.** `lightgbm.LGBMClassifier(objective="multiclass", n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=10, class_weight="balanced")`.
- **Quantile regressors.** Three `LGBMRegressor(objective="quantile", alpha=q)` models for q ∈ {0.1, 0.5, 0.9}, with `n_estimators=200` and `num_leaves=7` (data is small). Sort the predictions afterwards so that q10 ≤ q50 ≤ q90.
- **Cross-validation.** Leave-one-year-out (LOYO): for each year Y, train on all other years and test on Y. **Also recompute baselines and anomalies without year Y** inside each fold, or note the small leak honestly.
- **Metrics.**
  - Classifier: accuracy, macro-F1 and confusion matrix vs persistence and "always healthy"; also a Brier score for calibration.
  - Regressors: MAE of q50 vs a "predict 0" baseline, plus the q10–q90 interval coverage (target 80%).
- **Fallback.** If you have fewer than 60 yield rows, ship only the classifier and return `yield_anomaly_pct: null`.

## 7. Artifacts you must deliver (`services/agri/models/gbm_v1/`)
| file | content |
|---|---|
| `classifier.txt` | `booster_.save_model()` LightGBM text model (or `classifier.joblib`) |
| `reg_q10.txt`, `reg_q50.txt`, `reg_q90.txt` | quantile models (optional, but all three or none) |
| `baselines.parquet` | `region_id, doy_bin, ndvi_mu, ndvi_sigma, ndvi_min, ndvi_max, rain_normal_mm, sm_mu, sm_sigma, n_years`, used at inference for z, VCI and anomalies |
| `latest_features.parquet` | the last known feature row per region (for degraded mode) |
| `cv_report.json` | metrics per fold plus totals plus baselines |
| `metadata.json` | **contract below** |

```json
{
  "model_version": "gbm_v1",
  "created_at": "2026-10-02T18:00:00Z",
  "classifier_features": ["ndvi_mean","ndvi_anomaly_z","vci","ndvi_delta","rain_anomaly_pct","rain_30d_mm",
                          "soil_moisture_0_7cm","soil_moisture_anomaly_z","lst_anomaly_c","doy_bin"],
  "class_labels": ["healthy","watch","stressed","severe"],
  "target_lead_periods": 1,
  "period_days": 16,
  "regressor_features": ["ndvi_anom_mean","ndvi_anom_min","vci_min","vci_mean","rain_anomaly_pct","sm_anom_mean"],
  "regressor_quantiles": [0.1, 0.5, 0.9],
  "decision_dates": {"kharif":"09-15","rabi":"02-15"},
  "regions": ["MH-Yavatmal","MH-Latur","MP-Indore","MP-Ujjain","GJ-Rajkot","PB-Ludhiana","KA-Kalaburagi","RJ-Jodhpur"],
  "training_years": [2012, 2025],
  "data_source": "MODIS MOD13Q1 v061 NDVI 250m 16-day; ERA5-Land soil moisture via Open-Meteo",
  "label_source": "rule-based stress_class (VCI + rain deficit), see 09 §5; yield from ICRISAT DLD",
  "cv": {"scheme":"leave-one-year-out","clf_macro_f1":0.58,"persistence_macro_f1":0.49,"always_healthy_macro_f1":0.21,
         "reg_q50_mae":7.9,"zero_baseline_mae":10.4,"q10_q90_coverage":0.77},
  "feature_stats": {"ndvi_anomaly_z":{"mean":0.0,"std":1.0}}
}
```
The CV numbers above are **placeholders**; put in your real ones. `predict.py` must read **feature order and class order from `metadata.json`** and never hard-code them.

## 8. Service logic (`/agri_signal`)
1. Resolve `region_id` (404 if unknown) and find the period containing `date`. If `as_of` is set, use the latest period ending on or before `as_of`.
2. Load the raster `data/agri/rasters/<source>/<region_id>/<period_end>.tif`, or a zonal CSV row, and compute zonal statistics with `rasterstats.zonal_stats(geom, tif, stats=["mean","max","std","count"], nodata=...)`. Run this in `asyncio.to_thread`.
3. Fetch weather features (cached): Open-Meteo archive for the district centroid. Fall back to ingestion `/weather/features`, then NASA POWER.
4. Build features using `baselines.parquet`, then predict the class probabilities and quantiles.
5. Compute `trend` from the previous period's `ndvi_anomaly_z`.
6. Add `linked_equities` from `region_exposure.json`.
7. Compute `confidence` with the §1 formula.
8. **Degraded paths.**
   - Raster missing, or `chaos.agri_raster_missing`: use `latest_features.parquet` with `degraded=true`, `degraded_reason="raster_missing_last_cached"` and confidence × 0.5.
   - No cached features either: proxy mode using rain and soil features only, with the NDVI features set to NaN. LightGBM handles missing values natively. Use `degraded_reason="proxy_weather_only"` and cap confidence at 0.3.
9. `/agri_signal/batch {"region_ids":[...] | "tickers":[...]}`: when tickers are given, look up the regions from `region_exposure.json` and return one Evidence per region.

## 9. `data/region_exposure.json` (judgment-based mapping; say so)
```json
{
  "_note": "Judgment-based mapping of agri regions to listed equities. Not a measured sensitivity.",
  "regions": {
    "MP-Indore":   {"crops":["soybean","wheat"], "equities":[
        {"ticker":"ITC.NS","link":"agri-business / wheat sourcing","sign":"-","strength":0.3},
        {"ticker":"PATANJALI.NS","link":"edible oil (soy) input cost","sign":"-","strength":0.4},
        {"ticker":"UPL.NS","link":"crop protection demand","sign":"+","strength":0.2}]},
    "MH-Yavatmal": {"crops":["cotton","soybean"], "equities":[
        {"ticker":"M&M.NS","link":"rural tractor demand","sign":"-","strength":0.4},
        {"ticker":"KPRMILL.NS","link":"cotton input cost","sign":"-","strength":0.3}]},
    "PB-Ludhiana": {"crops":["rice","wheat"], "equities":[
        {"ticker":"KRBL.NS","link":"rice supply","sign":"-","strength":0.4},
        {"ticker":"CHAMBLFERT.NS","link":"fertiliser demand","sign":"+","strength":0.2}]},
    "GJ-Rajkot":   {"crops":["groundnut","cotton"], "equities":[
        {"ticker":"AWL.NS","link":"edible oil input cost","sign":"-","strength":0.3}]}
  },
  "sector_defaults": {"FMCG":0.5, "Agri":0.8, "Auto":0.3, "Banks":0.2, "Energy":0.1}
}
```
`sign` gives the direction of the equity's move when the region is **stressed**: "−" means stress hurts the stock. The exposure engine (06) reads `sector_defaults` as `agri_sens`.

## 10. Build prompt (paste into a coding LLM). Two parts.

**Part 1: training pipeline**
```
Build a training pipeline in services/agri/training/ for district crop-stress forecasting.
Inputs: either GeoTIFF NDVI rasters at data/agri/rasters/<source>/<region_id>/<YYYY-MM-DD>.tif with
data/agri/boundaries/districts.gpkg (column region_id), OR a zonal CSV exported from GEE
(columns region_id, period_end, NDVI_mean, NDVI_max, NDVI_stdDev, NDVI_count, EVI_mean).
Step 1. 02_zonal_stats.py: if rasters, compute rasterstats.zonal_stats(mean,max,std,count) per region/period;
        apply scale factor from CLI arg (--scale 0.0001 for MODIS) and nodata; valid_pixel_frac = count/total.
        Output data/agri/zonal.parquet with columns region_id, period_end, ndvi_mean, ndvi_max, ndvi_std, evi_mean, valid_pixel_frac.
Step 2. weather: fetch Open-Meteo archive (https://archive-api.open-meteo.com/v1/archive) daily precipitation_sum,
        soil_moisture_0_to_7cm (hourly → daily mean), temperature_2m_max for each region centroid from data/regions.json,
        cache to data/agri/weather/<region_id>.parquet.
Step 3. 03_build_table.py: build agri_training.csv EXACTLY with the columns in spec §4 (doy_bin for 16-day periods =
        ceil(doy/16)); anomalies computed leave-current-year-out; stress_class_now via rules §5;
        target_stress_class = next period's class within the same season; crop_season assignment Jun–Oct kharif,
        Nov–Mar rabi (season year rule). Build agri_yield_training.csv from data/agri/yield.csv
        (region_id,crop,year,yield_kg_ha) with leave-year-out linear trend, features aggregated up to decision dates.
        Also write baselines.parquet and latest_features.parquet.
Step 4. 04_train.py: LightGBM classifier + 3 quantile regressors with hyper-params in §6; leave-one-year-out CV;
        baselines persistence & always-healthy (classifier), zero (regressor); compute macro-F1, accuracy, Brier,
        MAE, q10–q90 coverage; save artifacts EXACTLY as §7 including metadata.json (feature order & class order).
        Skip regressors if < 60 yield rows (log it).
Step 5. 05_report.py: print a markdown table of CV metrics vs baselines and save confusion matrix PNG.
Output every file completely.
[PASTE §3–§7 OF 09_ISRO_AGRI.md]
```

**Part 2: service**
```
Build FastAPI service "agri" (port 8103) using copilot_common (Evidence, ToolResult, create_service_app, mock_or,
cached, EvidenceCounter, settings).
Step 1. agri/predict.py: class AgriModel loads services/agri/models/<MODEL_VERSION or gbm_v1>/ — metadata.json,
        classifier, optional regressors, baselines.parquet, latest_features.parquet. Feature & class order ONLY from metadata.
        predict(features: dict) -> (class_probs dict in order healthy,watch,stressed,severe, quantiles dict|None).
Step 2. agri/features.py + zonal.py + weather_feats.py per spec §8 steps 1-5 (CPU work in asyncio.to_thread).
Step 3. agri/exposure.py: linked_equities from data/region_exposure.json.
Step 4. main.py endpoints: POST /agri_signal (AgriReq{region_id,date,crop_season?,as_of?,chaos?,run_id?}) -> ToolResult
        with value EXACTLY like spec §1 example; POST /agri_signal/batch; GET /regions; GET /model_info (metadata.json).
        Degraded paths per §8.8. Confidence formula per §1. summary one-liner written by code.
Step 5. tests: predict reads feature order from metadata (shuffle columns test); degraded path when raster missing;
        as_of never uses later periods; output validates as ToolResult; MOCK=1 returns fixture.
Output every file completely.
[PASTE §1, §7, §8, §9 OF 09_ISRO_AGRI.md AND §5–7 OF 01_CONTRACTS.md]
```

## 11. Mock mode
With `MOCK=1`, `/agri_signal` returns `fixtures/agri/agri_signal.json` (the §1 example) with `degraded_reason="mock"`. **Ship the mock first (hour 1)**, so the orchestrator and UI can integrate before any imagery has been downloaded.

## 12. Acceptance checklist
- [ ] Data access to the chosen source has been confirmed in the first hour, and the decision (A, B or C) is written in `training/01_download.md`
- [ ] `agri_training.csv` has 8 regions × at least 8 years × the in-season periods, with no NaN in `ndvi_anomaly_z` for valid rows
- [ ] `cv_report.json` exists, the classifier is compared with persistence, and the result is reported honestly
- [ ] `/agri_signal` p95 is under 500 ms (cached weather)
- [ ] Deleting the raster gives `degraded=true` with a last cached value, and confidence is halved
- [ ] `metadata.json` matches §7, and the service refuses to start (clear error) if the feature list doesn't match the model

## 13. Integration hooks
- **Orchestrator `agri_agent` (04).** Runs when the Intent has `event_type ∈ {monsoon, heatwave, cyclone}` or a holding's sector is in {FMCG, Agri, Auto, Fertiliser}. It calls `/agri_signal/batch {"tickers": portfolio tickers}` and sends each Evidence to narrator **P5** (gemma3:4b). P5 must mention the yield range and that this is a slow signal.
- **Exposure (06).** `agri_sens` comes from `region_exposure.json` `sector_defaults`.
- **Monitor (11).** `agri_stress` alert when `stress_class` moves to stressed or severe on a region linked to a holding (Tier 2).
- **Terminal (13).** Region card with the NDVI anomaly sparkline, the class-probability bar and the yield fan (q10/q50/q90). Use the official Survey of India boundary if a map is drawn.
- **Backtest (12).** The `monsoon_deficit_2015` holdout can use `as_of` replay of agri features.

## 14. Sources
- Bhoonidhi portal: https://bhoonidhi.nrsc.gov.in/
- Bhoonidhi API spec: https://bhoonidhi.nrsc.gov.in/bhoonidhi-api/
- RS2 AWiFS NDVI composite: https://bhoonidhi.nrsc.gov.in/bhoonidhi_resources/help/docs/RS2_AWiFS_NDVI_Time_Composite_Product_v1.0.pdf
- bhoonidhi-downloader: https://github.com/geovicco-dev/bhoonidhi-downloader
- MOD13Q1: https://lpdaac.usgs.gov/products/mod13q1v061/
- GEE catalog `MODIS/061/MOD13Q1`, `COPERNICUS/S2_SR_HARMONIZED`
- NASA AppEEARS: https://appeears.earthdatacloud.nasa.gov/
- Open-Meteo Historical API: https://open-meteo.com/en/docs/historical-weather-api
- NASA POWER: https://power.larc.nasa.gov/
- ICRISAT DLD: http://data.icrisat.org/dld/
- VCI: Kogan (1995)
