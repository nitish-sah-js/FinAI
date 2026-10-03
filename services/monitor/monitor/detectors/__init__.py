"""Detectors (11 §3): pure functions, each returns list[Candidate]. Inputs are prepared by monitor.stats."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Candidate(BaseModel):
    kind: str                       # Alert.kind
    tickers: list[str]
    severity: float                 # 0..1
    relevance: float                # 0..1
    confidence: float               # 0..1
    facts: dict
    evidence_ids: list[str] = Field(default_factory=list)
    key: str = ""                   # cooldown subject: ticker, or region_id for weather/agri

    def cooldown_key(self) -> str:
        return f"{self.kind}:{self.key or self.tickers[0]}"


from .agri import detect_agri_stress                    # noqa: E402
from .news_burst import detect_news_burst               # noqa: E402
from .price_volume import detect_price_z, detect_volume_z  # noqa: E402
from .sentiment import detect_sentiment_shift           # noqa: E402
from .weather import detect_weather_threshold, weather_kinds  # noqa: E402

__all__ = ["Candidate", "detect_agri_stress", "detect_news_burst", "detect_price_z", "detect_volume_z",
           "detect_sentiment_shift", "detect_weather_threshold", "weather_kinds"]
