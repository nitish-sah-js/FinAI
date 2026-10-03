from detectors import Candidate

def impact_score(c: Candidate, weights: dict[str, float]) -> float:
    exposure = min(1.0, sum(weights.get(t, 0.0) for t in c.tickers) / 0.25)
    return round(min(1.0, c.severity * c.relevance * (0.4 + 0.6 * exposure)), 3)

def tier(impact: float, confidence: float) -> int:
    if impact >= 0.6 and confidence >= 0.6: return 3
    if impact >= 0.3: return 2
    return 1
