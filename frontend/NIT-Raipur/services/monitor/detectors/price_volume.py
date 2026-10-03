import math
from . import Candidate

def detect_price_z(prices, thresholds):
    # prices is expected to be a list of dicts: {'ticker': str, 'r_5m': float, 'sigma_5m': float, 'history_days': int}
    candidates = []
    thresh = thresholds.get('price_z', 3.0)
    for p in prices:
        if p['sigma_5m'] == 0:
            continue
        z = p['r_5m'] / p['sigma_5m']
        if abs(z) >= thresh:
            severity = min(abs(z) / 6.0, 1.0)
            conf = 0.8 if p.get('history_days', 0) >= 20 else 0.5
            candidates.append(Candidate(
                kind='price_z',
                tickers=[p['ticker']],
                severity=severity,
                relevance=1.0,
                confidence=conf,
                facts={'z': round(z, 2), 'r_5m': round(p['r_5m'], 4)},
                evidence_ids=[]
            ))
    return candidates

def detect_volume_z(volumes, thresholds):
    # volumes: [{'ticker': str, 'v_30m': float, 'mean_log_v': float, 'std_log_v': float}]
    candidates = []
    thresh = thresholds.get('volume_z', 3.0)
    for v in volumes:
        if v['v_30m'] <= 0 or v['std_log_v'] == 0:
            continue
        log_v = math.log(v['v_30m'])
        z = (log_v - v['mean_log_v']) / v['std_log_v']
        if z >= thresh:
            severity = min(z / 6.0, 1.0)
            candidates.append(Candidate(
                kind='volume_z',
                tickers=[v['ticker']],
                severity=severity,
                relevance=1.0,
                confidence=0.7,
                facts={'z': round(z, 2), 'volume': v['v_30m']},
                evidence_ids=[]
            ))
    return candidates
