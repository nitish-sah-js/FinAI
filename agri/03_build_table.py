from pathlib import Path
import numpy as np
import pandas as pd


# ============================================================
# AGRI / ISRO
# STEP 3 — BUILD TRAINING TABLE
#
# Input:
#   data/agri/modis/agri_modis_zonal_2011_2025.csv
#   data/agri/weather/agri_weather_2011_2025_ALL.csv
#
# Output:
#   data/agri/agri_training.csv
#   data/agri/baselines.parquet
#   data/agri/latest_features.parquet
# ============================================================


ROOT = Path("data/agri")

MODIS_FILE = ROOT / "modis" / "agri_modis_zonal_2011_2025.csv"
WEATHER_FILE = ROOT / "weather" / "agri_weather_2011_2025_ALL.csv"

TRAINING_FILE = ROOT / "agri_training.csv"
BASELINES_FILE = ROOT / "baselines.parquet"
LATEST_FILE = ROOT / "latest_features.parquet"


# ------------------------------------------------------------
# REGION MAPPING
# ------------------------------------------------------------

REGION_MAP = {
    ("Maharashtra", "Yavatmal"): "MH-Yavatmal",
    ("Maharashtra", "Latur"): "MH-Latur",
    ("Madhya Pradesh", "Indore"): "MP-Indore",
    ("Madhya Pradesh", "Ujjain"): "MP-Ujjain",
    ("Gujarat", "Rajkot"): "GJ-Rajkot",
    ("Punjab", "Ludhiana"): "PB-Ludhiana",

    # GEE/GAUL stores this district as Gulbarga.
    ("Karnataka", "Gulbarga"): "KA-Kalaburagi",

    ("Rajasthan", "Jodhpur"): "RJ-Jodhpur",
}


EXPECTED_REGIONS = set(REGION_MAP.values())


# ------------------------------------------------------------
# INPUT VALIDATION
# ------------------------------------------------------------

for required_file in (MODIS_FILE, WEATHER_FILE):
    if not required_file.exists():
        raise FileNotFoundError(
            f"Required file not found: {required_file}"
        )


# ------------------------------------------------------------
# LOAD MODIS
# ------------------------------------------------------------

print("=" * 70)
print("LOADING MODIS")
print("=" * 70)

modis = pd.read_csv(MODIS_FILE)

required_modis = {
    "ADM1_NAME",
    "ADM2_NAME",
    "image_date",
    "period_end",
    "NDVI_mean",
    "NDVI_max",
    "NDVI_stdDev",
    "EVI_mean",
    "EVI_max",
    "EVI_stdDev",
    "NDVI_count",
}

missing_modis = required_modis - set(modis.columns)
if missing_modis:
    raise ValueError(
        f"Missing MODIS columns: {sorted(missing_modis)}"
    )

modis["image_date"] = pd.to_datetime(
    modis["image_date"],
    errors="coerce",
)

modis["period_end"] = pd.to_datetime(
    modis["period_end"],
    errors="coerce",
)

if modis[["image_date", "period_end"]].isna().any().any():
    raise ValueError(
        "MODIS contains invalid date values."
    )

modis["region_id"] = [
    REGION_MAP.get((state, district))
    for state, district in zip(
        modis["ADM1_NAME"],
        modis["ADM2_NAME"],
    )
]

if modis["region_id"].isna().any():
    bad = (
        modis.loc[
            modis["region_id"].isna(),
            ["ADM1_NAME", "ADM2_NAME"],
        ]
        .drop_duplicates()
    )

    raise ValueError(
        "Unknown MODIS regions:\n"
        f"{bad.to_string(index=False)}"
    )

modis_regions = set(modis["region_id"].unique())

if modis_regions != EXPECTED_REGIONS:
    raise ValueError(
        "MODIS region mismatch.\n"
        f"Expected: {sorted(EXPECTED_REGIONS)}\n"
        f"Found: {sorted(modis_regions)}"
    )

# The current GEE export has NDVI_count but no total-pixel count.
# Therefore we explicitly keep valid_pixel_frac unavailable.
modis["valid_pixel_frac"] = np.nan

print(f"Rows: {len(modis):,}")
print(f"Regions: {modis['region_id'].nunique()}")


# ------------------------------------------------------------
# LOAD WEATHER
# ------------------------------------------------------------

print()
print("=" * 70)
print("LOADING WEATHER")
print("=" * 70)

weather = pd.read_csv(
    WEATHER_FILE,
    parse_dates=["date"],
)

required_weather = {
    "region",
    "date",
    "temperature_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "relative_humidity_2m_mean",
    "et0_fao_evapotranspiration",
    "soil_moisture_0_to_7cm_mean",
    "soil_moisture_7_to_28cm_mean",
    "soil_moisture_28_to_100cm_mean",
    "soil_temperature_0_to_7cm_mean",
    "soil_temperature_7_to_28cm_mean",
}

missing_weather = required_weather - set(weather.columns)
if missing_weather:
    raise ValueError(
        f"Missing weather columns: {sorted(missing_weather)}"
    )

weather = weather.rename(
    columns={"region": "region_id"}
)

weather_regions = set(weather["region_id"].dropna().unique())

if weather_regions != EXPECTED_REGIONS:
    raise ValueError(
        "Weather region mismatch.\n"
        f"Expected: {sorted(EXPECTED_REGIONS)}\n"
        f"Found: {sorted(weather_regions)}"
    )

weather["date"] = pd.to_datetime(
    weather["date"],
    errors="coerce",
)

if weather["date"].isna().any():
    raise ValueError(
        "Weather contains invalid dates."
    )

weather = weather.sort_values(
    ["region_id", "date"]
).reset_index(drop=True)

print(f"Rows: {len(weather):,}")
print(f"Regions: {weather['region_id'].nunique()}")


# ------------------------------------------------------------
# WEATHER FEATURE ENGINEERING
# ------------------------------------------------------------

print()
print("Building weather features...")


def build_weather_features(
    group: pd.DataFrame,
    region_id: str,
) -> pd.DataFrame:

    group = group.copy()
    group = group.sort_values("date").reset_index(drop=True)

    # Make the grouping key explicit. This avoids the pandas
    # GroupBy.apply behavior that can remove the key column.
    group["region_id"] = region_id

    # 30-day rainfall ending on each day.
    group["rain_30d_mm"] = (
        group["precipitation_sum"]
        .rolling(
            window=30,
            min_periods=30,
        )
        .sum()
    )

    month = group["date"].dt.month

    group["crop_season"] = np.select(
        [
            month.between(6, 10),
            month.isin([11, 12, 1, 2, 3]),
        ],
        [
            "kharif",
            "rabi",
        ],
        default="none",
    )

    # Rabi Nov-Dec belongs to the following year's season.
    group["season_year"] = group["date"].dt.year.astype(int)

    nov_dec = month.isin([11, 12])
    group.loc[nov_dec, "season_year"] += 1

    # Season-to-date rainfall.
    group["rain_season_to_date_mm"] = np.nan

    for (season_year, crop_season), idx in group.groupby(
        ["season_year", "crop_season"]
    ).groups.items():

        if crop_season == "none":
            continue

        season_idx = list(idx)

        values = (
            group.loc[
                season_idx,
                "precipitation_sum"
            ]
            .fillna(0)
            .cumsum()
        )

        group.loc[
            season_idx,
            "rain_season_to_date_mm"
        ] = values.to_numpy()

    return group


weather_parts = []

for region_id, region_df in weather.groupby(
    "region_id",
    sort=False,
):

    processed = build_weather_features(
        region_df,
        region_id,
    )

    weather_parts.append(processed)

weather = pd.concat(
    weather_parts,
    ignore_index=True,
)

weather = weather.sort_values(
    ["region_id", "date"]
).reset_index(drop=True)

# Critical validation before aggregation.
if "region_id" not in weather.columns:
    raise RuntimeError(
        "Internal error: region_id disappeared "
        "during weather feature construction."
    )


# ------------------------------------------------------------
# AGGREGATE DAILY WEATHER TO EACH MODIS PERIOD
# ------------------------------------------------------------

print()
print("Aggregating daily weather into MODIS periods...")

weather_lookup = {}

for region_id, region_df in weather.groupby(
    "region_id",
    sort=False,
):

    weather_lookup[region_id] = (
        region_df
        .sort_values("date")
        .reset_index(drop=True)
    )


weather_rows = []

for _, row in modis.iterrows():

    region = row["region_id"]
    start = row["image_date"]
    end = row["period_end"]

    region_weather = weather_lookup[region]

    w = region_weather[
        (region_weather["date"] >= start)
        & (region_weather["date"] <= end)
    ].copy()

    base = {
        "region_id": region,
        "image_date": start,
        "period_end": end,
    }

    if w.empty:
        weather_rows.append(base)
        continue

    base.update({
        "temperature_2m_mean":
            w["temperature_2m_mean"].mean(),

        "temperature_2m_max":
            w["temperature_2m_max"].max(),

        "temperature_2m_min":
            w["temperature_2m_min"].min(),

        "precipitation_period_mm":
            w["precipitation_sum"].sum(),

        "rain_30d_mm":
            w["rain_30d_mm"].iloc[-1],

        "relative_humidity_2m_mean":
            w["relative_humidity_2m_mean"].mean(),

        "et0_fao_evapotranspiration":
            w["et0_fao_evapotranspiration"].sum(),

        "soil_moisture_0_7cm":
            w["soil_moisture_0_to_7cm_mean"].mean(),

        "soil_moisture_7_28cm":
            w["soil_moisture_7_to_28cm_mean"].mean(),

        "soil_moisture_28_100cm":
            w["soil_moisture_28_to_100cm_mean"].mean(),

        "soil_temperature_0_7cm":
            w["soil_temperature_0_to_7cm_mean"].mean(),

        "soil_temperature_7_28cm":
            w["soil_temperature_7_to_28cm_mean"].mean(),

        "rain_season_to_date_mm":
            w["rain_season_to_date_mm"].iloc[-1],
    })

    weather_rows.append(base)


weather_period = pd.DataFrame(
    weather_rows
)


# ------------------------------------------------------------
# MERGE MODIS + WEATHER
# ------------------------------------------------------------

df = modis.merge(
    weather_period,
    on=[
        "region_id",
        "image_date",
        "period_end",
    ],
    how="left",
    validate="one_to_one",
)


if len(df) != len(modis):
    raise RuntimeError(
        "Merge changed the MODIS row count. "
        f"MODIS={len(modis)}, merged={len(df)}"
    )


# ------------------------------------------------------------
# CROP SEASON
# ------------------------------------------------------------

month = df["period_end"].dt.month

df["crop_season"] = np.select(
    [
        month.between(6, 10),
        month.isin([11, 12, 1, 2, 3]),
    ],
    [
        "kharif",
        "rabi",
    ],
    default="none",
)

df["season_year"] = (
    df["period_end"]
    .dt.year
    .astype(int)
)

nov_dec = month.isin([11, 12])

df.loc[
    nov_dec,
    "season_year"
] += 1

df = df[
    df["crop_season"].isin(
        ["kharif", "rabi"]
    )
].copy()


# ------------------------------------------------------------
# DAY-OF-YEAR BIN
# ------------------------------------------------------------

df["doy"] = (
    df["period_end"]
    .dt.dayofyear
)

df["doy_bin"] = (
    np.ceil(
        df["doy"] / 16
    )
    .astype(int)
    .clip(1, 23)
)


# ------------------------------------------------------------
# NDVI DELTA
# ------------------------------------------------------------

df = df.sort_values(
    [
        "region_id",
        "period_end",
    ]
).reset_index(drop=True)

df["ndvi_delta"] = (
    df
    .groupby("region_id")["NDVI_mean"]
    .diff()
)


# ------------------------------------------------------------
# LEAVE-CURRENT-YEAR-OUT BASELINE HELPER
# ------------------------------------------------------------

def leave_year_out_stats(
    data: pd.DataFrame,
    value_col: str,
    group_cols: list[str],
) -> pd.DataFrame:
    """
    For each (group_cols, season_year) combination, compute leave-current-
    year-out statistics from all OTHER years.

    Step 1: collapse multiple rows per (group_cols + season_year) to one mean.
            This prevents duplicate keys when merging back onto df, because
            MODIS 16-day periods can produce >1 row per doy_bin per year.
    Step 2: leave-year-out loop over the collapsed yearly means.
    """

    yearly = (
        data[
            group_cols
            + ["season_year", value_col]
        ]
        .dropna(subset=[value_col])
        .groupby(
            group_cols + ["season_year"],
            as_index=False,
        )[value_col]
        .mean()
        # ↑ one row per (group_cols, season_year) — duplicates gone
    )

    records = []

    for keys, group in yearly.groupby(
        group_cols,
        sort=False,
    ):

        if not isinstance(keys, tuple):
            keys = (keys,)

        values = group[
            value_col
        ].to_numpy(dtype=float)

        years = group[
            "season_year"
        ].to_numpy()

        for i in range(len(values)):

            others = np.delete(
                values,
                i,
            )

            if len(others) == 0:
                continue

            record = {
                col: value
                for col, value in zip(
                    group_cols,
                    keys,
                )
            }

            record["season_year"] = years[i]
            record["other_mean"] = float(
                np.mean(others)
            )

            record["other_std"] = (
                float(np.std(
                    others,
                    ddof=1,
                ))
                if len(others) > 1
                else np.nan
            )

            record["other_min"] = float(
                np.min(others)
            )

            record["other_max"] = float(
                np.max(others)
            )

            record["other_n"] = int(
                len(others)
            )

            records.append(record)

    if not records:
        return pd.DataFrame(
            columns=(
                group_cols
                + [
                    "season_year",
                    "other_mean",
                    "other_std",
                    "other_min",
                    "other_max",
                    "other_n",
                ]
            )
        )

    return pd.DataFrame(records)


# ------------------------------------------------------------
# NDVI ANOMALY + VCI
# ------------------------------------------------------------

print(
    "Calculating NDVI anomalies and VCI..."
)

ndvi_stats = leave_year_out_stats(
    df,
    "NDVI_mean",
    [
        "region_id",
        "doy_bin",
    ],
)

df = df.merge(
    ndvi_stats,
    on=[
        "region_id",
        "doy_bin",
        "season_year",
    ],
    how="left",
    validate="many_to_one",
)

df["ndvi_anomaly_z"] = (
    (
        df["NDVI_mean"]
        - df["other_mean"]
    )
    /
    df["other_std"].replace(
        0,
        np.nan,
    )
)

df["vci"] = (
    (
        df["NDVI_mean"]
        - df["other_min"]
    )
    /
    (
        df["other_max"]
        - df["other_min"]
    ).replace(
        0,
        np.nan,
    )
).clip(0, 1)

df = df.drop(
    columns=[
        "other_mean",
        "other_std",
        "other_min",
        "other_max",
        "other_n",
    ]
)


# ------------------------------------------------------------
# SOIL-MOISTURE ANOMALY
# ------------------------------------------------------------

print(
    "Calculating soil-moisture anomalies..."
)

sm_stats = leave_year_out_stats(
    df,
    "soil_moisture_0_7cm",
    [
        "region_id",
        "doy_bin",
    ],
)

df = df.merge(
    sm_stats,
    on=[
        "region_id",
        "doy_bin",
        "season_year",
    ],
    how="left",
    validate="many_to_one",
)

df["soil_moisture_anomaly_z"] = (
    (
        df["soil_moisture_0_7cm"]
        - df["other_mean"]
    )
    /
    df["other_std"].replace(
        0,
        np.nan,
    )
)

df = df.drop(
    columns=[
        "other_mean",
        "other_std",
        "other_min",
        "other_max",
        "other_n",
    ]
)


# ------------------------------------------------------------
# RAINFALL ANOMALY
# ------------------------------------------------------------

print(
    "Calculating rainfall anomalies..."
)

rain_stats = leave_year_out_stats(
    df,
    "rain_season_to_date_mm",
    [
        "region_id",
        "doy_bin",
    ],
)

df = df.merge(
    rain_stats,
    on=[
        "region_id",
        "doy_bin",
        "season_year",
    ],
    how="left",
    validate="many_to_one",  # fixed: df can have >1 row per (region,doy_bin,year)
)

df["rain_normal_mm"] = (
    df["other_mean"]
)

df["rain_anomaly_pct"] = (
    100.0
    * (
        df["rain_season_to_date_mm"]
        - df["rain_normal_mm"]
    )
    /
    df["rain_normal_mm"].replace(
        0,
        np.nan,
    )
)

df = df.drop(
    columns=[
        "other_mean",
        "other_std",
        "other_min",
        "other_max",
        "other_n",
    ]
)


# ------------------------------------------------------------
# STRESS LABEL
# ------------------------------------------------------------

def stress_label(row):

    vci = row["vci"]
    rain = row["rain_anomaly_pct"]
    ndvi_z = row["ndvi_anomaly_z"]

    if pd.isna(vci) or pd.isna(rain):
        return np.nan

    # Severe
    if (
        vci < 0.20
        and rain <= -30
    ):
        return "severe"

    # Stressed
    if (
        vci < 0.35
        or (
            not pd.isna(ndvi_z)
            and ndvi_z <= -1.0
            and rain <= -20
        )
    ):
        return "stressed"

    # Watch
    if (
        vci < 0.50
        or (
            not pd.isna(ndvi_z)
            and ndvi_z <= -0.5
        )
    ):
        return "watch"

    return "healthy"


print(
    "Creating stress labels..."
)

df["stress_class_now"] = df.apply(
    stress_label,
    axis=1,
)


# ------------------------------------------------------------
# NEXT-PERIOD TARGET
# ------------------------------------------------------------

df = df.sort_values(
    [
        "region_id",
        "season_year",
        "crop_season",
        "period_end",
    ]
).reset_index(drop=True)

target_group = [
    "region_id",
    "season_year",
    "crop_season",
]

df["target_stress_class"] = (
    df
    .groupby(target_group)["stress_class_now"]
    .shift(-1)
)

next_period_date = (
    df
    .groupby(target_group)["period_end"]
    .shift(-1)
)

gap_days = (
    next_period_date
    - df["period_end"]
).dt.days

# Avoid making a target across a large seasonal gap.
df.loc[
    gap_days > 24,
    "target_stress_class",
] = np.nan


# ------------------------------------------------------------
# LST
# ------------------------------------------------------------
# Not downloaded. Optional in the specification.
# We keep it explicitly missing rather than creating a proxy.

df["lst_anomaly_c"] = np.nan


# ------------------------------------------------------------
# FINAL COLUMNS
# ------------------------------------------------------------

final_columns = [
    "region_id",
    "ADM2_NAME",
    "ADM1_NAME",
    "crop_season",
    "season_year",

    "period_end",
    "image_date",
    "doy_bin",

    "NDVI_mean",
    "NDVI_max",
    "NDVI_stdDev",

    "EVI_mean",
    "EVI_max",
    "EVI_stdDev",

    "valid_pixel_frac",

    "ndvi_anomaly_z",
    "vci",
    "ndvi_delta",

    "rain_30d_mm",
    "rain_season_to_date_mm",
    "rain_normal_mm",
    "rain_anomaly_pct",

    "soil_moisture_0_7cm",
    "soil_moisture_anomaly_z",

    "temperature_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",

    "stress_class_now",
    "target_stress_class",

    "lst_anomaly_c",
]

df = df[
    final_columns
].copy()

df = df.sort_values(
    [
        "region_id",
        "period_end",
    ]
).reset_index(drop=True)


# ------------------------------------------------------------
# SAVE TRAINING TABLE
# ------------------------------------------------------------

df.to_csv(
    TRAINING_FILE,
    index=False,
)


# ------------------------------------------------------------
# FULL BASELINES FOR INFERENCE
# ------------------------------------------------------------

baselines = (
    df
    .groupby(
        [
            "region_id",
            "doy_bin",
        ],
        as_index=False,
    )
    .agg(
        ndvi_mu=(
            "NDVI_mean",
            "mean",
        ),

        ndvi_sigma=(
            "NDVI_mean",
            "std",
        ),

        ndvi_min=(
            "NDVI_mean",
            "min",
        ),

        ndvi_max=(
            "NDVI_mean",
            "max",
        ),

        rain_normal_mm=(
            "rain_season_to_date_mm",
            "mean",
        ),

        sm_mu=(
            "soil_moisture_0_7cm",
            "mean",
        ),

        sm_sigma=(
            "soil_moisture_0_7cm",
            "std",
        ),

        n_years=(
            "season_year",
            "nunique",
        ),
    )
)

baselines.to_parquet(
    BASELINES_FILE,
    index=False,
)


# ------------------------------------------------------------
# LATEST FEATURE ROW PER REGION
# ------------------------------------------------------------

latest = (
    df
    .sort_values("period_end")
    .groupby("region_id")
    .tail(1)
    .reset_index(drop=True)
)

latest.to_parquet(
    LATEST_FILE,
    index=False,
)


# ------------------------------------------------------------
# REPORT
# ------------------------------------------------------------

print()
print("=" * 70)
print("TRAINING TABLE COMPLETE")
print("=" * 70)

print(
    f"Rows: {len(df):,}"
)

print(
    f"Regions: {df['region_id'].nunique()}"
)

print(
    "Date range:",
    df["period_end"].min().date(),
    "->",
    df["period_end"].max().date(),
)

print()
print("Season counts:")
print(
    df["crop_season"].value_counts()
)

print()
print("Current stress distribution:")
print(
    df["stress_class_now"]
    .value_counts(dropna=False)
)

print()
print("Target distribution:")
print(
    df["target_stress_class"]
    .value_counts(dropna=False)
)

print()
print(
    "Missing target rows:",
    int(
        df["target_stress_class"].isna().sum()
    )
)

print()
print(
    f"Saved: {TRAINING_FILE}"
)

print(
    f"Saved: {BASELINES_FILE}"
)

print(
    f"Saved: {LATEST_FILE}"
)