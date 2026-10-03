import os
import yaml

base = "d:/Some_stuffs/Codeutsava X.0/NIT-Raipur/services/monitor"

# Price and Volume
with open(f"{base}/detectors/price_volume.py", "w") as f:
    f.write("""import math
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
""")

# News Burst
with open(f"{base}/detectors/news_burst.py", "w") as f:
    f.write("""from . import Candidate
import math

def detect_news_burst(news_stats, thresholds):
    # news_stats: [{'ticker': str, 'k': int, 'lambda_h': float, 'sector_match': bool}]
    candidates = []
    thresh_z = thresholds.get('news_burst_z', 3.0)
    thresh_k = thresholds.get('news_burst_k', 3)
    for n in news_stats:
        k = n['k']
        lam = max(n['lambda_h'], 0.5)
        z = (k - lam) / math.sqrt(lam)
        if z >= thresh_z and k >= thresh_k:
            severity = min(z / 8.0, 1.0)
            relevance = 0.5 if n.get('sector_match', False) else 1.0
            candidates.append(Candidate(
                kind='news_burst',
                tickers=[n['ticker']],
                severity=severity,
                relevance=relevance,
                confidence=0.6,
                facts={'k': k, 'lambda': round(lam, 2), 'z': round(z, 2)},
                evidence_ids=[]
            ))
    return candidates
""")

# Sentiment Shift
with open(f"{base}/detectors/sentiment.py", "w") as f:
    f.write("""from . import Candidate

def detect_sentiment_shift(sentiments, thresholds):
    # sentiments: [{'ticker': str, 'mean_6h': float, 'mean_24h': float, 'n_6h': int, 'conf': float}]
    candidates = []
    thresh_shift = thresholds.get('sentiment_shift', 0.4)
    thresh_n = thresholds.get('sentiment_min_n', 3)
    for s in sentiments:
        delta = s['mean_6h'] - s['mean_24h']
        if abs(delta) >= thresh_shift and s['n_6h'] >= thresh_n:
            severity = min(abs(delta), 1.0)
            candidates.append(Candidate(
                kind='sentiment_shift',
                tickers=[s['ticker']],
                severity=severity,
                relevance=1.0,
                confidence=s['conf'],
                facts={'delta': round(delta, 2), 'mean_6h': round(s['mean_6h'], 2), 'n_6h': s['n_6h']},
                evidence_ids=[]
            ))
    return candidates
""")

# Weather
with open(f"{base}/detectors/weather.py", "w") as f:
    f.write("""from . import Candidate

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
""")

# Agri
with open(f"{base}/detectors/agri.py", "w") as f:
    f.write("""from . import Candidate

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
""")

# Dedupe
with open(f"{base}/dedupe.py", "w") as f:
    f.write("""from datetime import datetime, timedelta

class CooldownManager:
    def __init__(self):
        # { cooldown_key: {'expires': datetime, 'tier': int, 'alert_id': str, 'kind': str, 'timestamp': datetime} }
        self.active = {}
        
    def get_window_minutes(self, kind: str) -> int:
        if kind in ('price_z', 'volume_z'): return 30
        if kind == 'news_burst': return 60
        if kind == 'sentiment_shift': return 120
        if kind == 'weather_threshold': return 6 * 60
        if kind == 'agri_stress': return 24 * 60
        return 30
        
    def check(self, kind: str, ticker_or_region: str, tier: int):
        key = f"{kind}:{ticker_or_region}"
        now = datetime.utcnow()
        
        # Cleanup expired
        to_del = [k for k, v in self.active.items() if v['expires'] <= now]
        for k in to_del: del self.active[k]
            
        if key in self.active:
            existing = self.active[key]
            if tier > existing['tier']:
                # Escalation
                self.active[key] = {'expires': now + timedelta(minutes=self.get_window_minutes(kind)), 'tier': tier, 'timestamp': now}
                return 'escalate', existing.get('alert_id')
            return 'update', existing.get('alert_id')
            
        self.active[key] = {'expires': now + timedelta(minutes=self.get_window_minutes(kind)), 'tier': tier, 'timestamp': now}
        return 'new', None
""")

print("Logic files updated.")
