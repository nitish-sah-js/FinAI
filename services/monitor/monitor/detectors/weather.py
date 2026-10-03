from __future__ import annotations

from . import Candidate

KINDS = ("cyclone", "hurricane", "heavy_rain", "heatwave", "rain_deficit")
_KEYWORDS = {"cyclone": "cyclone", "hurricane": "hurricane", "heavy rain": "heavy_rain", "heavy_rain": "heavy_rain",
             "heatwave": "heatwave", "heat wave": "heatwave", "deficit": "rain_deficit"}


def weather_kinds(value: dict) -> list[str]:
    """Normalise weather.alerts (10 §6 kinds, or free-text IMD bulletins) plus an attached storm into kinds."""
    kinds: list[str] = []
    for a in value.get("alerts") or []:
        a_l = str(a).lower()
        if a_l in KINDS:
            kinds.append(a_l)
            continue
        kinds += [k for word, k in _KEYWORDS.items() if word in a_l]
    storm = value.get("storm")
    if storm:
        basin = str(storm.get("basin", "")).lower() if isinstance(storm, dict) else ""
        kinds.append("hurricane" if "atlantic" in basin or "gulf" in basin else "cyclone")
    return list(dict.fromkeys(kinds))


def detect_weather_threshold(weather: list[dict], region_links: dict[str, list[dict]], severity: dict,
                             held: set[str] | None = None) -> list[Candidate]:
    """weather: [{region, kinds, confidence, evidence_id, facts}] → ONE candidate per region (cooldown is per region)
    with every linked ticker; relevance = strongest link among held tickers."""
    out = []
    for w in weather:
        sev = max((severity.get(k, 0.0) for k in w.get("kinds", [])), default=0.0)
        links = [l for l in region_links.get(w["region"], []) if held is None or l["ticker"] in held]
        if sev <= 0 or not links:
            continue
        out.append(Candidate(
            kind="weather_threshold", tickers=[l["ticker"] for l in links], key=w["region"],
            severity=sev, relevance=max(l["strength"] for l in links),
            confidence=float(w.get("confidence") if w.get("confidence") is not None else 0.5),
            facts={"region": w["region"], "alerts": w["kinds"],
                   "links": {l["ticker"]: l["strength"] for l in links}, **(w.get("facts") or {})},
            evidence_ids=[w["evidence_id"]] if w.get("evidence_id") else []))
    return out
