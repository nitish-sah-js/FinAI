"""STEP 4 - stress classifier + leave-one-year-out CV (fixed copy of agri/04_train_model.py).

Input : data/agri/agri_training.csv (from build_table.py)
Output: services/agri/models/<version>/ classifier.txt, classifier.joblib, baselines.parquet,
        latest_features.parquet, cv_report.json, metadata.json   (09 §7)

Kept from the teammate: features, class order, LightGBM hyper-parameters (spec §6 values were tested with
--params spec and did not do better, see README), random_state=42, class_weight="balanced", the
"all features present" row mask, persistence / always-healthy baselines, Brier score.

Fixed:
  * ndvi_delta is the season-safe column from build_table.py for BOTH the CV and the final model (the original CV
    recomputed a season-safe delta while the final model trained on the unsafe one -> train/serve skew; the CV
    recomputation also diffed across a held-out year when that year was removed).
  * fold baselines use agri.features.add_anomalies (yearly means per region/doy_bin, the same code the service and
    build_table use) with reference = all in-season composites of the training years only.
  * paths relative to this file; metadata records hyper-parameters, the delta definition and the persistence verdict.

Run:  .venv/Scripts/python services/agri/training/train_model.py [--version gbm_v2] [--params spec] [--out DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score

from _paths import DATA_AGRI, MODELS_DIR

from agri.features import add_anomalies

warnings.filterwarnings("ignore")

TRAINING_FILE = DATA_AGRI / "agri_training.csv"

FEATURES = ["stress_class_now_id", "NDVI_mean", "ndvi_anomaly_z", "vci", "ndvi_delta", "rain_anomaly_pct",
            "rain_30d_mm", "soil_moisture_0_7cm", "soil_moisture_anomaly_z", "doy_bin"]
CLASSES = ["healthy", "watch", "stressed", "severe"]
CLASS_TO_ID = {c: i for i, c in enumerate(CLASSES)}

PARAMS = {
    # teammate's values (agri/04_train_model.py). NB subsample has no effect without subsample_freq > 0.
    "teammate": dict(objective="multiclass", n_estimators=100, learning_rate=0.05, num_leaves=7, max_depth=4,
                     min_child_samples=20, subsample=0.8, colsample_bytree=0.8, class_weight="balanced",
                     random_state=42, verbosity=-1),
    # 09 §6 values
    "spec": dict(objective="multiclass", n_estimators=300, learning_rate=0.05, num_leaves=15, min_child_samples=10,
                 class_weight="balanced", random_state=42, verbosity=-1),
}


def brier(y, p) -> float:
    oh = np.zeros_like(p)
    oh[np.arange(len(y)), y] = 1.0
    return float(np.mean(np.sum((p - oh) ** 2, axis=1)))


def aligned_proba(model, X) -> np.ndarray:
    raw = model.predict_proba(X)
    full = np.zeros((len(X), len(CLASSES)))
    for col, cls in enumerate(model.classes_):
        full[:, int(cls)] = raw[:, col]
    return full


def model_frame(rows: pd.DataFrame) -> pd.DataFrame:
    X = pd.DataFrame(index=rows.index)
    X["stress_class_now_id"] = rows["stress_class_now"].map(CLASS_TO_ID).astype(float)
    for f in FEATURES[1:]:
        X[f] = rows[f].astype(float)
    return X[FEATURES]


def metrics(y, pred) -> dict:
    return {"accuracy": float(accuracy_score(y, pred)),
            "macro_f1": float(f1_score(y, pred, labels=list(range(4)), average="macro", zero_division=0))}


def run(params_name: str, version: str, out_dir: Path, allow_nan_delta: bool, previous_cv: str | None = None) -> dict:
    params = PARAMS[params_name]
    table = pd.read_csv(TRAINING_FILE, parse_dates=["period_end", "image_date"])
    table["season_year"] = table["season_year"].astype(int)
    labelled = table[table["target_stress_class"].isin(CLASSES) & table["stress_class_now"].isin(CLASSES)].copy()
    required = [f for f in FEATURES if not (allow_nan_delta and f == "ndvi_delta")]

    years = sorted(labelled["season_year"].unique())
    all_y, all_pred, all_pers, all_p, folds = [], [], [], [], []
    for test_year in years:
        ref = table[table["season_year"] != test_year]                 # fold baselines: training years only
        tr = add_anomalies(labelled[labelled["season_year"] != test_year], ref)
        te = add_anomalies(labelled[labelled["season_year"] == test_year], ref)
        Xtr, Xte = model_frame(tr), model_frame(te)
        mtr, mte = Xtr[required].notna().all(axis=1), Xte[required].notna().all(axis=1)
        tr, te, Xtr, Xte = tr[mtr], te[mte], Xtr[mtr], Xte[mte]
        if tr.empty or te.empty:
            continue
        ytr = tr["target_stress_class"].map(CLASS_TO_ID).astype(int).to_numpy()
        yte = te["target_stress_class"].map(CLASS_TO_ID).astype(int).to_numpy()
        model = lgb.LGBMClassifier(**params).fit(Xtr, ytr)
        p = aligned_proba(model, Xte)
        pred = p.argmax(axis=1)
        pers = te["stress_class_now"].map(CLASS_TO_ID).astype(int).to_numpy()   # persistence: next = now
        mm, pm, hm = metrics(yte, pred), metrics(yte, pers), metrics(yte, np.zeros_like(yte))
        folds.append({"year": int(test_year), "n_test": int(len(te)), "model_accuracy": mm["accuracy"],
                      "model_macro_f1": mm["macro_f1"], "model_brier": brier(yte, p),
                      "persistence_accuracy": pm["accuracy"], "persistence_macro_f1": pm["macro_f1"],
                      "always_healthy_accuracy": hm["accuracy"], "always_healthy_macro_f1": hm["macro_f1"]})
        print(f"  {test_year}: n={len(te):3d} model acc={mm['accuracy']:.3f} F1={mm['macro_f1']:.3f} | "
              f"persistence acc={pm['accuracy']:.3f} F1={pm['macro_f1']:.3f}")
        all_y.extend(yte); all_pred.extend(pred); all_pers.extend(pers); all_p.append(p)

    y, pred, pers, P = np.array(all_y), np.array(all_pred), np.array(all_pers), np.vstack(all_p)
    mm, pm, hm = metrics(y, pred), metrics(y, pers), metrics(y, np.zeros_like(y))
    beats = bool(mm["accuracy"] > pm["accuracy"] and mm["macro_f1"] > pm["macro_f1"])
    cv = {"scheme": "leave-one-year-out", "n_test_rows": int(len(y)),
          "model_accuracy": mm["accuracy"], "model_macro_f1": mm["macro_f1"], "model_brier": brier(y, P),
          "persistence_accuracy": pm["accuracy"], "persistence_macro_f1": pm["macro_f1"],
          "always_healthy_accuracy": hm["accuracy"], "always_healthy_macro_f1": hm["macro_f1"],
          "beats_persistence": beats,
          "confusion_matrix": confusion_matrix(y, pred, labels=list(range(4))).tolist(),
          "persistence_confusion_matrix": confusion_matrix(y, pers, labels=list(range(4))).tolist()}
    print(f"\nCV {params_name}: model acc={mm['accuracy']:.4f} F1={mm['macro_f1']:.4f} brier={cv['model_brier']:.4f} | "
          f"persistence acc={pm['accuracy']:.4f} F1={pm['macro_f1']:.4f} | always-healthy F1={hm['macro_f1']:.4f}"
          f" | beats persistence: {beats}")

    # ---------------- final model: leave-current-year-out features of the whole table (= what the service builds)
    Xall = model_frame(labelled)
    mask = Xall[required].notna().all(axis=1)
    Xf, yf = Xall[mask], labelled.loc[mask, "target_stress_class"].map(CLASS_TO_ID).astype(int)
    final = lgb.LGBMClassifier(**params).fit(Xf, yf)
    importance = dict(sorted(((f, int(v)) for f, v in zip(FEATURES, final.feature_importances_)),
                             key=lambda kv: -kv[1]))

    out_dir.mkdir(parents=True, exist_ok=True)
    final.booster_.save_model(str(out_dir / "classifier.txt"))
    joblib.dump(final, out_dir / "classifier.joblib")
    for name in ("baselines.parquet", "latest_features.parquet"):
        src = DATA_AGRI / name
        if src.exists():
            shutil.copyfile(src, out_dir / name)

    sha = hashlib.sha256(TRAINING_FILE.read_bytes()).hexdigest()[:16]
    metadata = {
        "model_version": version,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "classifier_features": FEATURES,
        "class_labels": CLASSES,
        "target_lead_periods": 1,
        "period_days": 16,
        "regions": sorted(labelled["region_id"].unique().tolist()),
        "training_years": [int(v) for v in sorted(labelled["season_year"].unique())],
        "n_training_rows": int(len(Xf)),
        "training_table_sha256_16": sha,
        "hyperparameters": {"preset": params_name, **{k: v for k, v in params.items() if k != "verbosity"}},
        "missing_ndvi_delta_allowed": allow_nan_delta,
        "data_source": "MODIS MOD13Q1 v061 NDVI/EVI 250m 16-day district zonal statistics (GEE export) + "
                       "Open-Meteo ERA5 daily weather at the district centroid",
        "label_source": "Rule-based stress_class (VCI + rainfall deficit, 09 §5); no yield labels",
        "feature_definitions": {
            "ndvi_delta": "NDVI_mean - previous composite NDVI_mean; NaN if the previous composite is in another "
                          "crop_season/season_year or > 24 days earlier (season-safe; fixed vs gbm_v1)",
            "anomalies": "leave-current-year-out per (region_id, doy_bin) on yearly means (agri/features.py)",
            "doy_bin": "clip(ceil(doy(period_end)/16), 1, 23)",
            "stress_class_now_id": "index of the 09 §5 rule label in class_labels",
        },
        "regressor_features": [],
        "regressor_quantiles": [],
        "cv": cv,
        "feature_importance_split": importance,
        "notes": [
            "Yield data was not supplied, so no yield quantile regressors: yield_anomaly_pct is null.",
            "LST was not supplied and is excluded from classifier features.",
            "LOYO folds recompute NDVI, VCI, rainfall and soil-moisture baselines from training years only.",
            ("The classifier does NOT beat the persistence baseline (next class = current class) in LOYO CV; "
             "the service lowers confidence accordingly.") if not beats else
            "The classifier beats the persistence baseline in LOYO CV on accuracy and macro-F1.",
        ],
    }
    if previous_cv and Path(previous_cv).is_file():
        prev = json.loads(Path(previous_cv).read_text(encoding="utf-8")).get("summary", {})
        metadata["previous_cv"] = {"source": str(previous_cv).replace("\\", "/"),
                                   "note": "teammate gbm_v1 (unsafe ndvi_delta in the final model; CV used a "
                                           "row-level season-safe delta)",
                                   **{k: prev.get(k) for k in ("model_accuracy", "model_macro_f1", "model_brier",
                                                               "persistence_accuracy", "persistence_macro_f1",
                                                               "always_healthy_macro_f1")}}
    (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (out_dir / "cv_report.json").write_text(json.dumps({"summary": cv, "folds": folds}, indent=2), encoding="utf-8")
    print(f"Final rows: {len(Xf)}  importance: {importance}\nSaved -> {out_dir}")
    return metadata


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="gbm_v2")
    ap.add_argument("--params", default="teammate", choices=sorted(PARAMS))
    ap.add_argument("--out", help="output dir (default services/agri/models/<version>)")
    ap.add_argument("--allow-nan-delta", action="store_true",
                    help="keep season-first rows (ndvi_delta NaN) in training/CV; LightGBM learns a missing branch")
    ap.add_argument("--previous-cv", help="cv_report.json of the previous model, embedded in metadata for comparison")
    a = ap.parse_args(argv)
    run(a.params, a.version, Path(a.out) if a.out else MODELS_DIR / a.version, a.allow_nan_delta, a.previous_cv)


if __name__ == "__main__":
    sys.exit(main())
