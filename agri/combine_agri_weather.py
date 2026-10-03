from pathlib import Path
import pandas as pd

WEATHER_DIR = Path("data/agri/weather")
OUTPUT_FILE = WEATHER_DIR / "agri_weather_2011_2025_ALL.csv"

# Read only the individual district weather files.
# This avoids accidentally reading the existing master/retry files.
files = sorted(WEATHER_DIR.glob("weather_*_2011_2025.csv"))

print(f"Found {len(files)} district files")

if len(files) != 8:
    raise RuntimeError(
        f"Expected 8 district files, found {len(files)}"
    )

frames = []

for file in files:
    print(f"Reading: {file.name}")

    df = pd.read_csv(file)

    if "region" not in df.columns:
        raise ValueError(
            f"'region' column missing in {file.name}"
        )

    frames.append(df)


# Combine
weather = pd.concat(
    frames,
    ignore_index=True
)


# Sort
weather["date"] = pd.to_datetime(weather["date"])

weather = weather.sort_values(
    ["region", "date"]
).reset_index(drop=True)


# Remove accidental duplicate observations
weather = weather.drop_duplicates(
    subset=["region", "date"]
).reset_index(drop=True)


# ------------------------------------------------------------
# VALIDATION
# ------------------------------------------------------------

print()
print("=" * 70)
print("WEATHER DATA VALIDATION")
print("=" * 70)

print(f"Total rows: {len(weather):,}")

print(
    f"Districts: {weather['region'].nunique()}"
)

print(
    "Date range:",
    weather["date"].min().date(),
    "->",
    weather["date"].max().date()
)

print()
print("Rows per district:")
print(
    weather.groupby("region")
    .size()
)

print()
print("Missing values:")
print(
    weather.isna().sum()
)


# ------------------------------------------------------------
# SAVE
# ------------------------------------------------------------

weather["date"] = weather["date"].dt.strftime(
    "%Y-%m-%d"
)

weather.to_csv(
    OUTPUT_FILE,
    index=False
)

print()
print(f"Saved master file:")
print(OUTPUT_FILE)