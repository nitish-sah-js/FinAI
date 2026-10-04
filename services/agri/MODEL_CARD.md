# Model card: crop-stress classifier `gbm_v2`

Evaluated 2026-10-04. Numbers come from `models/gbm_v2/cv_report.json` (leave-one-year-out cross-validation) and were
re-run with `training/train_model.py` on that date; they reproduce the stored report exactly.

## What it does
Predicts the crop-stress class of a district for the **next 16-day MODIS composite**: healthy, watch, stressed or severe.

| | |
|---|---|
| Model | LightGBM multiclass, class-balanced, random_state 42 |
| Coverage | 8 districts: Rajkot, Kalaburagi, Latur, Yavatmal, Indore, Ujjain, Ludhiana, Jodhpur |
| Inputs | MODIS MOD13Q1 v061 NDVI/EVI district statistics (Earth Engine export) + Open-Meteo ERA5 weather at the district centroid |
| Features | current class, NDVI mean, NDVI z-score, VCI, NDVI change, rain anomaly, 30-day rain, soil moisture and its z-score, season bin |
| Labels | **Rule-based** stress class (VCI + rainfall deficit). There are no yield or ground-truth labels. |
| Training rows | 1,745 composites, seasons 2011 to 2026 |

## How well it works (leave-one-year-out, 1,745 test rows)

| Method | Accuracy | Macro-F1 |
|---|---|---|
| **gbm_v2** | **0.605** | **0.552** |
| Persistence (next class = current class) | 0.624 | 0.553 |
| Always "healthy" | 0.532 | 0.174 |

**It does not beat persistence overall.** On accuracy and macro-F1, "nothing changes" is as good or better.

### Catching deteriorations
307 test composites (17.6%) got worse in the next period. Of those:

| Method | Recall (caught) | Precision (its "worse" calls that were right) |
|---|---|---|
| **gbm_v2** | **32.9%** (101 of 307) | **38.1%** (101 of 265 calls) |
| Persistence | 0% by construction | — |
| Always "one class worse" | 100% | 17.6% (the base rate) |

So the model has modest value as an **early warning**: about a third of real deteriorations are flagged, at roughly
twice the base-rate precision. It should not be used as a general forecast of crop condition.

## Known limits
- **Stale imagery.** The newest composite in `data/agri/modis` ends 2026-01-03. Every live answer is marked degraded
  (`stale_imagery`) with confidence halved until `scripts/refresh_modis.py` is run with Earthdata credentials.
- **No yield model.** `yield_anomaly_pct` is always null, so crop stress never becomes a portfolio shock in quant.
- **Rule-based labels.** The model learns to predict a rule applied to the next composite, not observed crop loss.
- **8 districts only.** Coastal and eastern paddy belts have no model; the orchestrator says so instead of borrowing one.

## How the system uses it
The service multiplies confidence by 0.8. The orchestrator gives the agri signal **low weight**: it never leads the
answer's bottom line, and any answer that uses it carries the dated caveat above.
