from . import Candidate
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
