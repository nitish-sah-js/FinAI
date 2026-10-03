"""Free-text region (from the Intent) → region_id used by the weather and agri tools.

Region ids follow 01 §6: <STATE>-<District> (OD-Puri) or <COUNTRY>-<Region> (US-GulfCoast).
The full list lives in ingestion's data/regions.json (10). This static map covers the demo regions.
"""
from __future__ import annotations

# keyword → (weather region_id, agri region_id or None).
# Agri ids must be districts the agri model (09, services/agri) covers: MH-Yavatmal, MH-Latur, MP-Indore, MP-Ujjain,
# GJ-Rajkot, PB-Ludhiana, KA-Kalaburagi, RJ-Jodhpur. Coastal/eastern paddy belts have no sound proxy → None (the agri
# agent then reports "no crop model" instead of silently answering for another district).
REGION_MAP: list[tuple[tuple[str, ...], str, str | None]] = [
    (("odisha", "orissa", "puri", "paradip", "bhubaneswar", "cuttack"), "OD-Puri", None),
    (("kutch", "gujarat", "mundra", "kandla"), "GJ-Kutch", "GJ-Rajkot"),
    (("chennai", "tamil nadu"), "TN-Chennai", None),
    (("andhra", "visakhapatnam", "vizag"), "AP-Visakhapatnam", "KA-Kalaburagi"),   # proxy: Deccan rain-fed
    (("west bengal", "kolkata", "bengal", "sundarbans"), "WB-Kolkata", None),
    (("vidarbha", "yavatmal", "maharashtra"), "MH-Yavatmal", "MH-Yavatmal"),
    (("mumbai",), "MH-Mumbai", None),
    (("ujjain",), "MP-Ujjain", "MP-Ujjain"),
    (("latur", "marathwada"), "MH-Latur", "MH-Latur"),
    (("kalaburagi", "gulbarga", "karnataka"), "KA-Kalaburagi", "KA-Kalaburagi"),
    (("jodhpur", "marwar"), "RJ-Jodhpur", "RJ-Jodhpur"),
    (("madhya pradesh", "indore", "malwa"), "MP-Indore", "MP-Indore"),
    (("punjab", "ludhiana", "haryana"), "PB-Ludhiana", "PB-Ludhiana"),
    (("rajasthan", "jaipur"), "RJ-Jaipur", "RJ-Jodhpur"),             # proxy: Rajasthan arid belt
    (("uttar pradesh", "lucknow", "up "), "UP-Lucknow", "PB-Ludhiana"),           # proxy: rice-wheat belt
    (("delhi", "north india", "ncr"), "DL-NewDelhi", "PB-Ludhiana"),
    (("gulf", "louisiana", "texas", "houston", "mexico"), "US-GulfCoast", None),
    (("bay of bengal", "east coast"), "OD-Puri", None),
    (("india", "all-india", "monsoon"), "IN-All", "MH-Yavatmal"),
]

DEFAULT_WEATHER = "IN-All"
DEFAULT_AGRI = "MH-Yavatmal"


def resolve(region: str | None, query: str = "") -> tuple[str, str | None]:
    """Return (weather_region_id, agri_region_id)."""
    text = f" {(region or '')} {query} ".lower()
    for keys, weather_id, agri_id in REGION_MAP:
        if any(k in text for k in keys):
            return weather_id, agri_id
    return DEFAULT_WEATHER, DEFAULT_AGRI


AGRI_KEYWORDS = ("district", "kharif", "rabi", "crop", "ndvi", "farm", "agri", "paddy", "soy", "cotton")


def is_agri_region(region: str | None, query: str = "") -> bool:
    text = f"{region or ''} {query}".lower()
    return any(k in text for k in AGRI_KEYWORDS)
