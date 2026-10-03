"""Impact score and tier routing (11 §4), exactly as specified."""
from __future__ import annotations

from .detectors import Candidate


def impact_score(c: Candidate, weights: dict[str, float]) -> float:
    exposure = min(1.0, sum(weights.get(t, 0.0) for t in c.tickers) / 0.25)   # 25%+ of book → full exposure
    return round(min(1.0, c.severity * c.relevance * (0.4 + 0.6 * exposure)), 3)


def tier(impact: float, confidence: float) -> int:
    if impact >= 0.6 and confidence >= 0.6:
        return 3        # popup + Telegram/email
    if impact >= 0.3:
        return 2        # popup with one-line reason
    return 1            # pet nudge only


def apply_watchlist(c: Candidate, held: set[str], watchlist: set[str], factor: float) -> Candidate | None:
    """Held tickers keep full relevance; watchlist-only tickers get relevance × factor; anything else is dropped."""
    if any(t in held for t in c.tickers):
        return c
    if any(t in watchlist for t in c.tickers):
        return c.model_copy(update={"relevance": round(c.relevance * factor, 3)})
    return None
