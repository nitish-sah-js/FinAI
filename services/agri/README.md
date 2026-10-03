# Agri service (L2 :8103, tool `agri`), docs/09

District crop-stress signal from **MODIS MOD13Q1 v061 NDVI** (GEE zonal-mean export; not ISRO imagery) plus
Open-Meteo ERA5 weather, for the 8 districts the model was trained on:
MH-Yavatmal, MH-Latur, MP-Indore, MP-Ujjain, GJ-Rajkot, PB-Ludhiana, KA-Kalaburagi, RJ-Jodhpur.

```
cd services/agri
..\..\.venv\Scripts\python -m uvicorn agri.main:app --host 0.0.0.0 --port 8103
..\..\.venv\Scripts\python -m pytest -q
```

| endpoint | body / notes |
|---|---|
| `POST /agri_signal` | `{region_id, date\|on_date?, crop_season?, as_of?, run_id?, chaos?}`; extra keys ignored. `X-Run-Id` → `ev_agri_NNN`; `X-Chaos: agri_raster_missing` supported |
| `POST /agri_signal/batch` | `{region_ids?\|tickers?, date?, as_of?, ...}`; tickers → regions via `data/region_exposure.json`; none → all 8 |
| `GET /regions` | metadata (district, state, lat/lon from the weather CSV, crops), latest composite, linked equities |
| `GET /model_info` | metadata.json + LOYO CV vs persistence + per-fold report + verdict |
| `GET /health` | `imagery` dep says "stale" while the newest composite is > 40 days old |

`MOCK=1` returns `copilot_common/fixtures/agri/agri_signal{,_batch}.json`.

## Honest status
* **No yield model** (no district yield labels) → `yield_anomaly_pct: null` + a warning. The quant scenario builder
  then adds no agri shock.
* **The classifier does not beat persistence** (LOYO CV, 1 758 rows): accuracy 0.605 vs persistence 0.624, macro-F1
  0.552 vs 0.553, Brier 0.510 (always-healthy F1 0.174). Confidence is multiplied by 0.8 (`model_skill` in every value).
* **Imagery ends 2026-01-03**: every live request is `degraded=true, degraded_reason="stale_imagery"` (confidence × 0.5).
* `stress_class` is the model's forecast for the next composite (~16 days); `stress_class_now` is the §5 rule label.

## Confidence
`max(class_probs) × min(1, valid_pixel_frac/0.7) × (0.7 if a model feature is missing) × skill_factor(0.8) × (0.5 if degraded)`,
capped at 0.3 when NDVI is missing (weather-only proxy). `valid_pixel_frac` = NDVI_count / the region's max count
(proxy; the export has no total-pixel count).

## Feature recipe (agri/features.py, shared by training and serving)
Latest in-season composite with `period_end <= min(date, as_of)`; `doy_bin = clip(ceil(doy(period_end)/16),1,23)`;
leave-current-year-out anomalies per (region, doy_bin) on yearly means; with `as_of` the baselines use only composites
with `period_end <= as_of` (strict time machine, tested by scrambling all later data). Without `as_of` a past `date`
uses all other years, as in training, and says so in a warning. Weather: rain_30d and season-to-date rain on the last day
`<= period_end`, soil-moisture mean over `[image_date, period_end]`.
The service rebuilds every row of `data/agri/agri_training.csv` exactly (test). The 09 §7 `baselines.parquet`
(all-years) is shipped but not used for inference: it includes the current year, which training excluded.

## Training (`training/`, paths resolve relative to the files)
```
.venv/Scripts/python services/agri/training/build_table.py --compare agri/data/agri/agri_training.csv
.venv/Scripts/python services/agri/training/train_model.py --previous-cv agri/services/agri/models/gbm_v1/cv_report.json
```
Fixes vs the teammate's `agri/03_build_table.py` / `04_train_model.py` (originals untouched):
1. `ndvi_delta` is season-safe (NaN if the previous composite is in another season or season year, or more than 24 days
   earlier). The original diffed across Apr–May and kharif→rabi in 240 rows (03:510-514), and the final model trained on
   those values while the CV used a safe delta. Fed the safe delta, gbm_v1 changes its class on 22 of those 240 rows.
2. CV fold baselines use the same yearly-mean recipe as the table and the service. The original's per-row
   recomputation also diffed across a held-out year.
3. Every other column of the rebuilt table matches the teammate's table exactly.

Hyper-parameters: kept the teammate's (100 trees, 7 leaves, depth 4, min_child 20, colsample 0.8, balanced,
seed 42). The 09 §6 preset (`--params spec`) was worse in CV: macro-F1 0.530, Brier 0.530. Keeping season-start rows with
NaN delta (`--allow-nan-delta`) was also worse (F1 0.545). The model never saw a NaN delta, so LightGBM treats one as 0.
A first-of-season composite therefore counts as "feature missing" (× 0.7).
Retraining is deterministic: three runs gave an identical `classifier.txt` (md5 f9adcab9…).

| LOYO CV | model acc | model F1 | persistence acc | persistence F1 |
|---|---|---|---|---|
| gbm_v1 (teammate) | 0.607 | 0.553 | 0.622 | 0.551 |
| gbm_v2 (this) | 0.605 | 0.552 | 0.624 | 0.553 |

`training/download_agri_weather.py` and `combine_agri_weather.py` are copies with fixed paths. They were not re-run.
