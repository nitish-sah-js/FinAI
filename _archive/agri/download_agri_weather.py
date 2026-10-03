import time
from pathlib import Path

import pandas as pd
import requests


# ============================================================
# AGRI / ISRO WEATHER DATA DOWNLOADER
# Open-Meteo Historical Weather API
# ERA5
#
# Period: 2011-01-01 -> 2025-12-31
# Regions: 8 target districts
# ============================================================

START_DATE = "2011-01-01"
END_DATE = "2025-12-31"

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

OUTPUT_DIR = Path("data/agri/weather")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------
# 1. TARGET DISTRICTS
# ------------------------------------------------------------

REGIONS = [
    {
        "region": "MH-Yavatmal",
        "query": "Yavatmal, Maharashtra, India",
    },
    {
        "region": "MH-Latur",
        "query": "Latur, Maharashtra, India",
    },
    {
        "region": "MP-Indore",
        "query": "Indore, Madhya Pradesh, India",
    },
    {
        "region": "MP-Ujjain",
        "query": "Ujjain, Madhya Pradesh, India",
    },
    {
        "region": "GJ-Rajkot",
        "query": "Rajkot, Gujarat, India",
    },
    {
        "region": "PB-Ludhiana",
        "query": "Ludhiana, Punjab, India",
    },
    {
        "region": "KA-Kalaburagi",
        "query": "Kalaburagi, Karnataka, India",
    },
    {
        "region": "RJ-Jodhpur",
        "query": "Jodhpur, Rajasthan, India",
    },
]


# ------------------------------------------------------------
# 2. WEATHER VARIABLES
# ------------------------------------------------------------

DAILY_VARIABLES = [
    "temperature_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",

    "precipitation_sum",
    "rain_sum",
    "precipitation_hours",

    "relative_humidity_2m_mean",

    "et0_fao_evapotranspiration",

    "soil_moisture_0_to_7cm_mean",
    "soil_moisture_7_to_28cm_mean",
    "soil_moisture_28_to_100cm_mean",

    "soil_temperature_0_to_7cm_mean",
    "soil_temperature_7_to_28cm_mean",
]


# ------------------------------------------------------------
# 3. HTTP SESSION
# ------------------------------------------------------------

session = requests.Session()

session.headers.update({
    "User-Agent": "Agri-Isro-Research/1.0"
})


# ------------------------------------------------------------
# 4. GEOCODE A DISTRICT
# ------------------------------------------------------------

def geocode_location(query: str) -> dict:
    params = {
        "name": query,
        "count": 10,
        "language": "en",
        "format": "json",
        "countryCode": "IN",
    }

    response = session.get(
        GEOCODING_URL,
        params=params,
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    results = data.get("results", [])

    if not results:
        raise RuntimeError(
            f"No geocoding result found for: {query}"
        )

    # Prefer an Indian result
    for result in results:
        if result.get("country_code") == "IN":
            return result

    return results[0]


# ------------------------------------------------------------
# 5. DOWNLOAD WEATHER
# ------------------------------------------------------------

def download_weather(
    region: str,
    query: str,
) -> pd.DataFrame:

    print()
    print("=" * 70)
    print(f"Processing: {region}")
    print("=" * 70)

    # --------------------------------------------------------
    # Geocoding
    # --------------------------------------------------------

    location = geocode_location(query)

    latitude = location["latitude"]
    longitude = location["longitude"]

    print(
        f"Location: {location.get('name')}, "
        f"{location.get('admin1')}"
    )

    print(
        f"Coordinates: "
        f"{latitude:.5f}, {longitude:.5f}"
    )


    # --------------------------------------------------------
    # API request
    # --------------------------------------------------------

    params = {
        "latitude": latitude,
        "longitude": longitude,

        "start_date": START_DATE,
        "end_date": END_DATE,

        "daily": ",".join(DAILY_VARIABLES),

        # India
        "timezone": "Asia/Kolkata",

        # Long-term consistent historical model
        "models": "era5",

        # Prefer land grid cells
        "cell_selection": "land",

        "temperature_unit": "celsius",
        "precipitation_unit": "mm",
    }


    # --------------------------------------------------------
    # Request with retry
    # --------------------------------------------------------

    last_error = None

    for attempt in range(3):

        try:

            response = session.get(
                ARCHIVE_URL,
                params=params,
                timeout=120,
            )

            response.raise_for_status()

            data = response.json()

            break

        except Exception as exc:

            last_error = exc

            print(
                f"Request failed "
                f"(attempt {attempt + 1}/3): {exc}"
            )

            time.sleep(3)

    else:
        raise RuntimeError(
            f"Failed downloading {region}: "
            f"{last_error}"
        )


    # --------------------------------------------------------
    # Parse daily data
    # --------------------------------------------------------

    daily = data.get("daily")

    if daily is None:
        raise RuntimeError(
            f"No daily weather data returned for {region}"
        )

    df = pd.DataFrame(daily)


    # --------------------------------------------------------
    # Add metadata
    # --------------------------------------------------------

    df.insert(
        0,
        "region",
        region,
    )

    df["latitude_requested"] = latitude
    df["longitude_requested"] = longitude

    df["weather_model"] = "ERA5"
    df["timezone"] = "Asia/Kolkata"

    df["source"] = "Open-Meteo Historical Weather API"


    # --------------------------------------------------------
    # Rename date
    # --------------------------------------------------------

    df = df.rename(
        columns={
            "time": "date"
        }
    )


    # --------------------------------------------------------
    # Save individual district file
    # --------------------------------------------------------

    safe_name = region.lower()

    district_file = (
        OUTPUT_DIR /
        f"weather_{safe_name}_2011_2025.csv"
    )

    df.to_csv(
        district_file,
        index=False,
    )

    print(
        f"Saved: {district_file}"
    )

    print(
        f"Rows: {len(df):,}"
    )

    return df


# ------------------------------------------------------------
# 6. DOWNLOAD ALL 8 DISTRICTS
# ------------------------------------------------------------

all_data = []

for item in REGIONS:

    try:

        df = download_weather(
            region=item["region"],
            query=item["query"],
        )

        all_data.append(df)

        # Small pause between requests
        time.sleep(1)

    except Exception as exc:

        print()
        print(
            f"ERROR for {item['region']}:"
        )
        print(exc)
        print()


# ------------------------------------------------------------
# 7. COMBINE EVERYTHING
# ------------------------------------------------------------

if not all_data:

    raise RuntimeError(
        "No weather data was downloaded."
    )


weather = pd.concat(
    all_data,
    ignore_index=True,
)


# Sort
weather = weather.sort_values(
    [
        "region",
        "date",
    ]
).reset_index(drop=True)


# ------------------------------------------------------------
# 8. SAVE MASTER DATASET
# ------------------------------------------------------------

master_file = (
    OUTPUT_DIR /
    "agri_weather_2011_2025.csv"
)

weather.to_csv(
    master_file,
    index=False,
)


# ------------------------------------------------------------
# 9. BASIC VALIDATION
# ------------------------------------------------------------

print()
print("=" * 70)
print("DOWNLOAD COMPLETE")
print("=" * 70)

print(
    f"Total rows: {len(weather):,}"
)

print(
    f"Districts: {weather['region'].nunique()}"
)

print(
    "Date range:",
    weather["date"].min(),
    "->",
    weather["date"].max(),
)

print()
print("Rows by district:")
print(
    weather.groupby("region")
    .size()
)

print()
print("Missing values:")
print(
    weather.isna()
    .sum()
)

print()
print(
    f"Master file saved to:\n{master_file}"
)