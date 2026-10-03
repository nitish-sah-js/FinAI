from . import Candidate

def detect_weather_threshold(weather_data, region_links):
    # weather_data: [{'region': str, 'alerts': list[str], 'conf': float}]
    candidates = []
    sev_map = {'cyclone': 0.9, 'hurricane': 0.9, 'heavy_rain': 0.6, 'heatwave': 0.5, 'rain_deficit': 0.5}
    for w in weather_data:
        region = w['region']
        if not w['alerts']:
            continue
        max_sev = max((sev_map.get(a, 0.0) for a in w['alerts']), default=0.0)
        if max_sev == 0.0:
            continue
            
        links = region_links.get(region, [])
        for ticker, strength in links:
            candidates.append(Candidate(
                kind='weather_threshold',
                tickers=[ticker],
                severity=max_sev,
                relevance=strength,
                confidence=w['conf'],
                facts={'region': region, 'alerts': w['alerts']},
                evidence_ids=[]
            ))
    return candidates
