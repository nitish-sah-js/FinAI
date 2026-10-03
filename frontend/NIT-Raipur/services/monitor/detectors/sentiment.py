from . import Candidate

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
