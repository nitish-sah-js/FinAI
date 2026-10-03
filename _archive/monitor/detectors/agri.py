from . import Candidate

def detect_agri_stress(agri_data, region_links):
    # agri_data: [{'region': str, 'stress_class': str, 'prev_class': str, 'conf': float}]
    candidates = []
    sev_map = {'stressed': 0.6, 'severe': 0.9}
    for a in agri_data:
        region = a['region']
        sc = a['stress_class']
        pc = a['prev_class']
        if sc in sev_map and pc not in sev_map:
            severity = sev_map[sc]
            links = region_links.get(region, [])
            for ticker, strength in links:
                candidates.append(Candidate(
                    kind='agri_stress',
                    tickers=[ticker],
                    severity=severity,
                    relevance=strength,
                    confidence=a['conf'],
                    facts={'region': region, 'stress_class': sc},
                    evidence_ids=[]
                ))
    return candidates
