"""Chaos switch: when FORCE_RATE_LIMIT is set, every cloud call fails with a fake 429 (03 §9 step 7)."""
from __future__ import annotations

import contextvars

FORCE_RATE_LIMIT: contextvars.ContextVar[bool] = contextvars.ContextVar("FORCE_RATE_LIMIT", default=False)


class FakeRateLimit(Exception):
    status_code = 429
    retry_after = 0.0          # 0 → no cooldown is persisted for a simulated limit
