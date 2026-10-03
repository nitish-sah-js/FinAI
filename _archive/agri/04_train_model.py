from pathlib import Path
import json
import warnings

import joblib
import numpy as np
import pandas as pd
import lightgbm as lgb

from sklearn.metrics import (
    accuracy_score,
    f1_score,
    confusion_matrix,
)

warnings.filterwarnings("ignore")


# ============================================================
# AGRI / ISRO
# STEP 4 — STRESS CLASSIFIER
#
# Implements the project specification:
#   - LightGBM multiclass classifier
#   - fixed class order: healthy, watch, stressed, severe
#   - leave-one-year-out (LOYO) CV
#   - fold-safe anomaly / VCI / rainfall / soil baselines
#   - persistence + always-healthy baselines
#   - Brier score
#   - final model + inference artifacts
#
# Yield data is not present, so this trains the classifier only.
# ============================================================


ROOT = Path("data/agri")
TRAINING_FILE = ROOT / "agri_training.csv"

MODEL_DIR = Path("services/agri/models/gbm_v1")
MODEL_DIR.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------
# MODEL CONTRACT
# ------------------------------------------------------------

FEATURES = [
    "stress_class_now_id",
    "NDVI_mean",
    "ndvi_anomaly_z",
    "vci",
    "ndvi_delta",
    "rain_anomaly_pct",
    "rain_30d_mm",
    "soil_moisture_0_7cm",
    "soil_moisture_anomaly_z",
    "doy_bin",
]

CLASSES = [
    "healthy",
    "watch",
    "stressed",
    "severe",
]

CLASS_TO_ID = {
    label: idx
    for idx, label in enumerate(CLASSES)
}


# ------------------------------------------------------------
# LOAD
# ------------------------------------------------------------

print("=" * 70)
print("LOADING TRAINING DATA")
print("=" * 70)

if not TRAINING_FILE.exists():
    raise FileNotFoundError(
        f"Training file not found: {TRAINING_FILE}"
    )

df = pd.read_csv(
    TRAINING_FILE,
    parse_dates=[
        "period_end",
        "image_date",
    ],
)

required_columns = {
    "region_id",
    "season_year",
    "period_end",
    "crop_season",
    "NDVI_mean",
    "ndvi_anomaly_z",
    "vci",
    "ndvi_delta",
    "rain_anomaly_pct",
    "rain_30d_mm",
    "rain_season_to_date_mm",
    "rain_normal_mm",
    "soil_moisture_0_7cm",
    "soil_moisture_anomaly_z",
    "doy_bin",
    "stress_class_now",
    "target_stress_class",
}

missing = required_columns - set(df.columns)

if missing:
    raise ValueError(
        f"Missing training columns: {sorted(missing)}"
    )


# ------------------------------------------------------------
# TARGET CLEANING
# ------------------------------------------------------------

df = df[
    df["target_stress_class"].isin(CLASSES)
].copy()

df = df[
    df["stress_class_now"].isin(CLASSES)
].copy()

df["season_year"] = (
    df["season_year"]
    .astype(int)
)

df["stress_class_now_id"] = (
    df["stress_class_now"]
    .map(CLASS_TO_ID)
    .astype(int)
)


print(
    f"Rows with target: {len(df):,}"
)

print(
    f"Regions: {df['region_id'].nunique()}"
)

print()
print("Target distribution:")
print(
    df["target_stress_class"]
    .value_counts()
)


# ------------------------------------------------------------
# BRIER SCORE
# ------------------------------------------------------------

def multiclass_brier(
    y_true,
    probabilities,
):
    one_hot = np.zeros_like(
        probabilities,
        dtype=float,
    )

    one_hot[
        np.arange(len(y_true)),
        y_true,
    ] = 1.0

    return float(
        np.mean(
            np.sum(
                (probabilities - one_hot) ** 2,
                axis=1,
            )
        )
    )


# ------------------------------------------------------------
# FOLD-SAFE FEATURE RECOMPUTATION
# ------------------------------------------------------------
# The source agri_training.csv contains leave-current-year-out
# features. For LOYO, using those values directly would still
# allow the held-out year's observations to influence training
# features. We therefore rebuild the anomaly/VCI/rainfall/soil
# baselines inside each fold.
#
# For training rows:
#   baseline excludes that row's season_year.
#
# For held-out rows:
#   baseline uses training years only.
# ------------------------------------------------------------


def prepare_group_arrays(
    reference: pd.DataFrame,
    value_col: str,
):
    """
    Build compact arrays keyed by (region_id, doy_bin).

    Each key stores:
        years
        values
    """

    arrays = {}

    for key, group in reference.groupby(
        ["region_id", "doy_bin"],
        sort=False,
    ):
        arrays[key] = (
            group["season_year"]
            .to_numpy(dtype=int),
            group[value_col]
            .to_numpy(dtype=float),
        )

    return arrays


def calculate_safe_features(
    rows: pd.DataFrame,
    reference: pd.DataFrame,
):
    """
    Compute fold-safe features for rows using reference data.

    For each row, all historical comparison values with the
    same region_id + doy_bin are considered. The row's own
    season_year is removed whenever that year is present in the
    reference set.
    """

    rows = rows.copy()

    ndvi_arrays = prepare_group_arrays(
        reference,
        "NDVI_mean",
    )

    rain_arrays = prepare_group_arrays(
        reference,
        "rain_season_to_date_mm",
    )

    sm_arrays = prepare_group_arrays(
        reference,
        "soil_moisture_0_7cm",
    )

    ndvi_z = []
    vci = []
    rain_anom = []
    sm_z = []

    for row in rows.itertuples(index=False):

        key = (
            row.region_id,
            int(row.doy_bin),
        )

        year = int(row.season_year)

        # ----------------------------
        # NDVI
        # ----------------------------

        n_years, n_values = ndvi_arrays.get(
            key,
            (np.array([], dtype=int),
             np.array([], dtype=float)),
        )

        # Exclude current year.
        mask = n_years != year
        other_ndvi = n_values[
            mask & np.isfinite(n_values)
        ]

        if len(other_ndvi) >= 2:

            mu = float(
                np.mean(other_ndvi)
            )

            sigma = float(
                np.std(
                    other_ndvi,
                    ddof=1,
                )
            )

            if sigma > 0:
                ndvi_z.append(
                    (
                        row.NDVI_mean
                        - mu
                    ) / sigma
                )
            else:
                ndvi_z.append(np.nan)

            ndvi_min = float(
                np.min(other_ndvi)
            )

            ndvi_max = float(
                np.max(other_ndvi)
            )

            if ndvi_max > ndvi_min:
                vci.append(
                    np.clip(
                        (
                            row.NDVI_mean
                            - ndvi_min
                        )
                        / (
                            ndvi_max
                            - ndvi_min
                        ),
                        0.0,
                        1.0,
                    )
                )
            else:
                vci.append(np.nan)

        else:
            ndvi_z.append(np.nan)
            vci.append(np.nan)


        # ----------------------------
        # Rainfall
        # ----------------------------

        r_years, r_values = rain_arrays.get(
            key,
            (np.array([], dtype=int),
             np.array([], dtype=float)),
        )

        other_rain = r_values[
            (r_years != year)
            & np.isfinite(r_values)
        ]

        if len(other_rain) > 0:
            rain_normal = float(
                np.mean(other_rain)
            )

            if rain_normal != 0:
                rain_anom.append(
                    100.0
                    * (
                        row.rain_season_to_date_mm
                        - rain_normal
                    )
                    / rain_normal
                )
            else:
                rain_anom.append(np.nan)
        else:
            rain_anom.append(np.nan)


        # ----------------------------
        # Soil moisture
        # ----------------------------

        s_years, s_values = sm_arrays.get(
            key,
            (np.array([], dtype=int),
             np.array([], dtype=float)),
        )

        other_sm = s_values[
            (s_years != year)
            & np.isfinite(s_values)
        ]

        if len(other_sm) >= 2:

            sm_mu = float(
                np.mean(other_sm)
            )

            sm_sigma = float(
                np.std(
                    other_sm,
                    ddof=1,
                )
            )

            if sm_sigma > 0:
                sm_z.append(
                    (
                        row.soil_moisture_0_7cm
                        - sm_mu
                    ) / sm_sigma
                )
            else:
                sm_z.append(np.nan)

        else:
            sm_z.append(np.nan)


    # --------------------------------------------------------
    # NDVI delta — recompute safely within each region and
    # invalidate the first period of every crop season.
    #
    # This prevents a delta from crossing the kharif -> rabi
    # or rabi -> kharif boundary.
    # --------------------------------------------------------

    ordered = (
        rows
        .sort_values(
            ["region_id", "period_end"]
        )
        .copy()
    )

    ordered["ndvi_delta_safe"] = (
        ordered
        .groupby("region_id")["NDVI_mean"]
        .diff()
    )

    season_changed = (
        ordered["crop_season"]
        != ordered
        .groupby("region_id")["crop_season"]
        .shift(1)
    )

    ordered.loc[
        season_changed,
        "ndvi_delta_safe"
    ] = np.nan

    ndvi_delta_safe = (
        ordered["ndvi_delta_safe"]
        .reindex(rows.index)
    )

    result = pd.DataFrame(
        {
            "stress_class_now_id":
                rows["stress_class_now"].map(CLASS_TO_ID).fillna(-1).astype(int).to_numpy(),

            "NDVI_mean":
                rows["NDVI_mean"].to_numpy(),

            "ndvi_anomaly_z":
                ndvi_z,

            "vci":
                vci,

            "ndvi_delta":
                ndvi_delta_safe.to_numpy(),

            "rain_anomaly_pct":
                rain_anom,

            "rain_30d_mm":
                rows["rain_30d_mm"].to_numpy(),

            "soil_moisture_0_7cm":
                rows[
                    "soil_moisture_0_7cm"
                ].to_numpy(),

            "soil_moisture_anomaly_z":
                sm_z,

            "doy_bin":
                rows["doy_bin"].to_numpy(),
        },
        index=rows.index,
    )

    return result


# ------------------------------------------------------------
# FULL PROBABILITY ALIGNMENT
# ------------------------------------------------------------

def aligned_probabilities(
    model,
    X,
):
    raw = model.predict_proba(X)

    full = np.zeros(
        (
            len(X),
            len(CLASSES),
        ),
        dtype=float,
    )

    for col, model_class in enumerate(
        model.classes_
    ):
        full[:, int(model_class)] = (
            raw[:, col]
        )

    return full


# ------------------------------------------------------------
# LOYO CROSS VALIDATION
# ------------------------------------------------------------

print()
print("=" * 70)
print("LEAVE-ONE-YEAR-OUT CROSS VALIDATION")
print("=" * 70)

years = sorted(
    df["season_year"]
    .unique()
)

print(
    f"Years: {years}"
)

all_true = []
all_pred = []
all_persistence = []
all_healthy = []
all_probabilities = []

fold_reports = []


for test_year in years:

    print()
    print(
        f"--- Hold out {test_year} ---"
    )

    train_rows = df[
        df["season_year"] != test_year
    ].copy()

    test_rows = df[
        df["season_year"] == test_year
    ].copy()

    # Fold-safe anomaly calculation.
    train_features = calculate_safe_features(
        train_rows,
        train_rows,
    )

    test_features = calculate_safe_features(
        test_rows,
        train_rows,
    )

    # Remove rows where a required comparison statistic
    # cannot be calculated.
    train_mask = (
        train_features[
            FEATURES
        ].notna().all(axis=1)
    )

    test_mask = (
        test_features[
            FEATURES
        ].notna().all(axis=1)
    )

    train_rows = train_rows.loc[
        train_mask
    ].copy()

    test_rows = test_rows.loc[
        test_mask
    ].copy()

    train_features = train_features.loc[
        train_mask
    ]

    test_features = test_features.loc[
        test_mask
    ]

    if train_rows.empty or test_rows.empty:
        print(
            "Skipped: insufficient fold data."
        )
        continue

    y_train = (
        train_rows["target_stress_class"]
        .map(CLASS_TO_ID)
        .astype(int)
        .to_numpy()
    )

    y_test = (
        test_rows["target_stress_class"]
        .map(CLASS_TO_ID)
        .astype(int)
        .to_numpy()
    )

    X_train = train_features[
        FEATURES
    ]

    X_test = test_features[
        FEATURES
    ]

    # --------------------------------------------------------
    # LightGBM parameters from the project spec.
    # --------------------------------------------------------

    model = lgb.LGBMClassifier(
        objective="multiclass",
        n_estimators=100,
        learning_rate=0.05,
        num_leaves=7,
        max_depth=4,
        min_child_samples=20,
        subsample=0.8,
        colsample_bytree=0.8,
        class_weight="balanced",
        random_state=42,
        verbosity=-1,
    )

    model.fit(
        X_train,
        y_train,
    )

    probabilities = aligned_probabilities(
        model,
        X_test,
    )

    predictions = np.argmax(
        probabilities,
        axis=1,
    )

    # --------------------------------------------------------
    # Baselines required by the spec.
    # --------------------------------------------------------

    persistence = (
        test_rows["stress_class_now"]
        .map(CLASS_TO_ID)
        .astype(int)
        .to_numpy()
    )

    always_healthy = np.zeros(
        len(test_rows),
        dtype=int,
    )

    model_acc = accuracy_score(
        y_test,
        predictions,
    )

    model_f1 = f1_score(
        y_test,
        predictions,
        labels=list(range(len(CLASSES))),
        average="macro",
        zero_division=0,
    )

    persistence_acc = accuracy_score(
        y_test,
        persistence,
    )

    persistence_f1 = f1_score(
        y_test,
        persistence,
        labels=list(range(len(CLASSES))),
        average="macro",
        zero_division=0,
    )

    healthy_acc = accuracy_score(
        y_test,
        always_healthy,
    )

    healthy_f1 = f1_score(
        y_test,
        always_healthy,
        labels=list(range(len(CLASSES))),
        average="macro",
        zero_division=0,
    )

    brier = multiclass_brier(
        y_test,
        probabilities,
    )

    print(
        f"n={len(test_rows):4d} | "
        f"model acc={model_acc:.3f} "
        f"F1={model_f1:.3f} | "
        f"persistence F1={persistence_f1:.3f}"
    )

    fold_reports.append(
        {
            "year": int(test_year),
            "n_test": int(len(test_rows)),
            "model_accuracy": float(model_acc),
            "model_macro_f1": float(model_f1),
            "model_brier": float(brier),
            "persistence_accuracy":
                float(persistence_acc),
            "persistence_macro_f1":
                float(persistence_f1),
            "always_healthy_accuracy":
                float(healthy_acc),
            "always_healthy_macro_f1":
                float(healthy_f1),
        }
    )

    all_true.extend(
        y_test
    )

    all_pred.extend(
        predictions
    )

    all_persistence.extend(
        persistence
    )

    all_healthy.extend(
        always_healthy
    )

    all_probabilities.append(
        probabilities
    )


if not all_true:
    raise RuntimeError(
        "LOYO produced no valid test predictions."
    )


all_true = np.asarray(
    all_true,
    dtype=int,
)

all_pred = np.asarray(
    all_pred,
    dtype=int,
)

all_persistence = np.asarray(
    all_persistence,
    dtype=int,
)

all_healthy = np.asarray(
    all_healthy,
    dtype=int,
)

all_probabilities = np.vstack(
    all_probabilities
)


# ------------------------------------------------------------
# OVERALL CV
# ------------------------------------------------------------

model_accuracy = accuracy_score(
    all_true,
    all_pred,
)

model_macro_f1 = f1_score(
    all_true,
    all_pred,
    labels=list(range(len(CLASSES))),
    average="macro",
    zero_division=0,
)

model_brier = multiclass_brier(
    all_true,
    all_probabilities,
)

persistence_accuracy = accuracy_score(
    all_true,
    all_persistence,
)

persistence_macro_f1 = f1_score(
    all_true,
    all_persistence,
    labels=list(range(len(CLASSES))),
    average="macro",
    zero_division=0,
)

always_healthy_accuracy = accuracy_score(
    all_true,
    all_healthy,
)

always_healthy_macro_f1 = f1_score(
    all_true,
    all_healthy,
    labels=list(range(len(CLASSES))),
    average="macro",
    zero_division=0,
)

cm = confusion_matrix(
    all_true,
    all_pred,
    labels=list(range(len(CLASSES))),
)


# ------------------------------------------------------------
# REPORT
# ------------------------------------------------------------

print()
print("=" * 70)
print("CROSS-VALIDATION RESULTS")
print("=" * 70)

print(
    f"Model accuracy             : "
    f"{model_accuracy:.4f}"
)

print(
    f"Model macro-F1             : "
    f"{model_macro_f1:.4f}"
)

print(
    f"Model Brier score          : "
    f"{model_brier:.4f}"
)

print()
print(
    f"Persistence accuracy       : "
    f"{persistence_accuracy:.4f}"
)

print(
    f"Persistence macro-F1       : "
    f"{persistence_macro_f1:.4f}"
)

print()
print(
    f"Always-healthy accuracy    : "
    f"{always_healthy_accuracy:.4f}"
)

print(
    f"Always-healthy macro-F1    : "
    f"{always_healthy_macro_f1:.4f}"
)

print()
print("Confusion matrix:")

print(
    pd.DataFrame(
        cm,
        index=[
            f"true_{c}"
            for c in CLASSES
        ],
        columns=[
            f"pred_{c}"
            for c in CLASSES
        ],
    )
)


# ------------------------------------------------------------
# TRAIN FINAL MODEL ON ALL AVAILABLE DATA
# ------------------------------------------------------------

print()
print("=" * 70)
print("TRAINING FINAL MODEL")
print("=" * 70)

# For the final model, use the already-created historical
# leave-current-year-out features in agri_training.csv.
#
# Rows with incomplete anomaly statistics are removed.

final_mask = (
    df[FEATURES]
    .notna()
    .all(axis=1)
)

final_df = df.loc[
    final_mask
].copy()

X_final = final_df[
    FEATURES
]

y_final = (
    final_df["target_stress_class"]
    .map(CLASS_TO_ID)
    .astype(int)
)

final_model = lgb.LGBMClassifier(
    objective="multiclass",
    n_estimators=100,
    learning_rate=0.05,
    num_leaves=7,
    max_depth=4,
    min_child_samples=20,
    subsample=0.8,
    colsample_bytree=0.8,
    class_weight="balanced",
    random_state=42,
    verbosity=-1,
)

final_model.fit(
    X_final,
    y_final,
)

print(
    f"Final training rows: "
    f"{len(final_df):,}"
)


# ------------------------------------------------------------
# FEATURE IMPORTANCE
# ------------------------------------------------------------

importance = (
    pd.Series(
        final_model.feature_importances_,
        index=FEATURES,
    )
    .sort_values(
        ascending=False
    )
)

print()
print("Feature importance:")
print(
    importance.to_string()
)


# ------------------------------------------------------------
# SAVE CLASSIFIER
# ------------------------------------------------------------

classifier_txt = (
    MODEL_DIR
    / "classifier.txt"
)

final_model.booster_.save_model(
    str(classifier_txt)
)

joblib.dump(
    final_model,
    MODEL_DIR / "classifier.joblib",
)


# ------------------------------------------------------------
# SAVE BASELINES
# ------------------------------------------------------------

baselines_file = (
    ROOT / "baselines.parquet"
)

latest_file = (
    ROOT / "latest_features.parquet"
)

if baselines_file.exists():
    baselines = pd.read_parquet(
        baselines_file
    )

    baselines.to_parquet(
        MODEL_DIR / "baselines.parquet",
        index=False,
    )

if latest_file.exists():
    latest = pd.read_parquet(
        latest_file
    )

    latest.to_parquet(
        MODEL_DIR / "latest_features.parquet",
        index=False,
    )


# ------------------------------------------------------------
# METADATA
# ------------------------------------------------------------

metadata = {
    "model_version": "gbm_v1",

    "classifier_features": FEATURES,

    "class_labels": CLASSES,

    "target_lead_periods": 1,

    "period_days": 16,

    "regions": sorted(
        df["region_id"]
        .unique()
        .tolist()
    ),

    "training_years": [
        int(year)
        for year in sorted(
            df["season_year"].unique()
        )
    ],

    "data_source": (
        "MODIS MOD13Q1 v061 NDVI/EVI 250m "
        "16-day district zonal statistics + "
        "Open-Meteo ERA5 daily weather"
    ),

    "label_source": (
        "Rule-based stress_class "
        "(VCI + rainfall deficit)"
    ),

    "cv": {
        "scheme": "leave-one-year-out",

        "model_accuracy":
            float(model_accuracy),

        "model_macro_f1":
            float(model_macro_f1),

        "model_brier":
            float(model_brier),

        "persistence_accuracy":
            float(persistence_accuracy),

        "persistence_macro_f1":
            float(persistence_macro_f1),

        "always_healthy_accuracy":
            float(always_healthy_accuracy),

        "always_healthy_macro_f1":
            float(always_healthy_macro_f1),

        "confusion_matrix":
            cm.tolist(),
    },

    "notes": [
        "Yield data was not supplied, so yield quantile regressors were not trained.",
        "LST was not supplied and is therefore excluded from classifier features.",
        "LOYO folds recompute NDVI, VCI, rainfall and soil-moisture baselines using training years only.",
        "Final model uses the full historical training table with leave-current-year-out features.",
    ],
}


with open(
    MODEL_DIR / "metadata.json",
    "w",
    encoding="utf-8",
) as f:
    json.dump(
        metadata,
        f,
        indent=2,
    )


# ------------------------------------------------------------
# CV REPORT
# ------------------------------------------------------------

cv_report = {
    "summary": metadata["cv"],
    "folds": fold_reports,
}


with open(
    MODEL_DIR / "cv_report.json",
    "w",
    encoding="utf-8",
) as f:
    json.dump(
        cv_report,
        f,
        indent=2,
    )


# ------------------------------------------------------------
# FINAL OUTPUT
# ------------------------------------------------------------

print()
print("=" * 70)
print("TRAINING COMPLETE")
print("=" * 70)

print(
    f"Model macro-F1 : "
    f"{model_macro_f1:.4f}"
)

print(
    f"Persistence F1 : "
    f"{persistence_macro_f1:.4f}"
)

print(
    f"Model accuracy : "
    f"{model_accuracy:.4f}"
)

print()
print("Artifacts:")

for path in sorted(
    MODEL_DIR.iterdir()
):
    print(
        f"  {path.name}"
    )
