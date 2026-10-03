from __future__ import annotations
from datetime import date, datetime, timezone
from typing import Any
from copilot_common.ids import EvidenceCounter
from copilot_common.models import Evidence

MODEL_VERSION = "quant_v1"


def as_of_dt(d: date | datetime | None) -> datetime:
    """Market data 'as of' = that day's NSE close (15:30 IST = 10:00Z), matching the 01/06 examples."""
    if d is None:
        return datetime.now(timezone.utc)
    if isinstance(d, datetime):
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return datetime(d.year, d.month, d.day, 10, 0, tzinfo=timezone.utc)


def make_evidence(run_id: str | None, tool: str, value: dict[str, Any], source: str, as_of: date | datetime | None,
                  confidence: float, degraded: bool = False, degraded_reason: str | None = None,
                  latency_ms: int | None = None, summary: str | None = None,
                  model_version: str = MODEL_VERSION) -> Evidence:
    """Confidence rule: if degraded, confidence is multiplied by 0.5 (06 §3)."""
    ts = datetime.now(timezone.utc)
    a = as_of_dt(as_of)
    conf = round(confidence * (0.5 if degraded else 1.0), 4)
    return Evidence(id=EvidenceCounter(run_id).next(tool), run_id=run_id, tool=tool, value=value, summary=summary,
                    source=source, as_of=a, timestamp=ts, freshness_s=max(0, int((ts - a).total_seconds())),
                    confidence=conf, degraded=degraded, degraded_reason=degraded_reason,
                    latency_ms=latency_ms, model_version=model_version)
