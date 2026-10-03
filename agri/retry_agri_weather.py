import time
from pathlib import Path

import pandas as pd
import requests


START_DATE = "2011-01-01"
END_DATE = "2025-12-31"

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

OUTPUT_DIR = Path("data/agri/weather")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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

REGIONS = [
    {
        "region": "MP-Ujjain",
        "query": "Ujjain, Madhya Pradesh, India",
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

session = requests.Session()
session.headers.update({
    "User-Agent": "Agri-Isro-Research/1.0"
})


def geocode_location(query):
    response = session.get(
        GEOCODING_URL,
        params={
            "name": query,
            "count": 10,
            "language": "en",
            "format": "json",
            "countryCode": "IN",
        },
        timeout=30,
    )

    response.raise_for_status()

    results = response.json().get("results", [])

    if not results:
        raise RuntimeError(f"No geocoding result for {query}")

    for result in results:
        if result.get("country_code") == "IN":
            return result

    return results[0]


def download_weather(region, query):

    print()
    print("=" * 70)
    print(f"Processing: {region}")
    print("=" * 70)

    location = geocode_location(query)

    latitude = location["latitude"]
    longitude = location["longitude"]

    print(
        f"Location: {location.get('name')}, "
        f"{location.get('admin1')}"
    )

    print(
        f"Coordinates: {latitude:.5f}, "
        f"{longitude:.5f}"
    )

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": START_DATE,
        "end_date": END_DATE,
        "daily": ",".join(DAILY_VARIABLES),
        "timezone": "Asia/Kolkata",
        "models": "era5",
        "cell_selection": "land",
        "temperature_unit": "celsius",
        "precipitation_unit": "mm",
    }

    # Retry with long pauses after rate limiting.
    for attempt in range(5):

        try:

            response = session.get(
                ARCHIVE_URL,
                params=params,
                timeout=120,
            )

            if response.status_code == 429:

                wait_time = 30 * (attempt + 1)

                print(
                    f"Rate limited. Waiting "
                    f"{wait_time} seconds..."
                )

                time.sleep(wait_time)
                continue

            response.raise_for_status()

            data = response.json()
            break

        except requests.RequestException as exc:

            print(
                f"Request failed "
                f"(attempt {attempt + 1}/5): {exc}"
            )

            if attempt == 4:
                raise

            time.sleep(20 * (attempt + 1))

    else:
        raise RuntimeError(
            f"Could not download {region}"
        )

    daily = data.get("daily")

    if daily is None:
        raise RuntimeError(
            f"No daily data returned for {region}"
        )

    df = pd.DataFrame(daily)

    df.insert(0, "region", region)

    df["latitude_requested"] = latitude
    df["longitude_requested"] = longitude
    df["weather_model"] = "ERA5"
    df["timezone"] = "Asia/Kolkata"
    df["source"] = "Open-Meteo Historical Weather API"

    df = df.rename(
        columns={"time": "date"}
    )

    output_file = (
        OUTPUT_DIR
        / f"weather_{region.lower()}_2011_2025.csv"
    )

    df.to_csv(
        output_file,
        index=False,
    )

    print(f"Saved: {output_file}")
    print(f"Rows: {len(df):,}")

    return df


# ------------------------------------------------------------
# DOWNLOAD ONLY THE 3 FAILED DISTRICTS
# ------------------------------------------------------------

downloaded = []

for item in REGIONS:

    try:

        df = download_weather(
            item["region"],
            item["query"],
        )

        downloaded.append(df)

        # Large gap before next API request.
        time.sleep(30)

    except Exception as exc:

        print()
        print(f"FAILED: {item['region']}")
        print(exc)


# ------------------------------------------------------------
# SAVE RETRY MASTER FILE
# ------------------------------------------------------------

if downloaded:

    retry_data = pd.concat(
        downloaded,
        ignore_index=True,
    )

    retry_data = retry_data.sort_values(
        ["region", "date"]
    ).reset_index(drop=True)

    retry_file = (
        OUTPUT_DIR
        / "agri_weather_retry_2011_2025.csv"
    )

    retry_data.to_csv(
        retry_file,
        index=False,
    )

    print()
    print("=" * 70)
    print("RETRY COMPLETE")
    print("=" * 70)

    print(
        f"Rows downloaded: {len(retry_data):,}"
    )

    print(
        retry_data.groupby("region").size()
    )

    print(
        f"Saved: {retry_file}"
    )

else:

    print("No district downloaded successfully.")