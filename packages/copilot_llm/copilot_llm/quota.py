"""QuotaTracker: rate-limit headers → state, 429 cooldowns, 402 disable, persisted to data/quota.json (03 §6)."""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Mapping

from copilot_common.settings import get_settings

_DUR = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?$")


def parse_duration(text: str | None) -> float | None:
    """'2m59.56s' → 179.56, '7.66s' → 7.66, '1h2m' → 3720, '120' → 120, '250ms' → 0.25."""
    if text is None:
        return None
    t = str(text).strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        pass
    m = _DUR.match(t)
    if not m or not any(m.groups()):
        return None
    h, mi, s, ms = (float(g) if g else 0.0 for g in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000


def _int(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


@dataclass
class ProviderQuota:
    requests_day_limit: int | None = None
    requests_day_remaining: int | None = None
    tokens_minute_limit: int | None = None
    tokens_minute_remaining: int | None = None
    tokens_reset_at: float | None = None       # epoch seconds when the TPM window resets
    cooldown_until: float = 0.0
    disabled_reason: str | None = None
    updated_at: float = 0.0
    used_session: int = field(default=0)


class QuotaTracker:
    def __init__(self, path: Path | None = None):
        self.path = path or get_settings().data_dir / "quota.json"
        self._lock = threading.Lock()
        self.state: dict[str, ProviderQuota] = {}
        self._load()

    # ---------- persistence ----------
    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for k, v in raw.items():
                q = ProviderQuota(**{f: v.get(f) for f in ProviderQuota.__dataclass_fields__ if f in v})
                q.disabled_reason = None      # 402 disable lasts one session only
                q.used_session = 0
                self.state[k] = q
        except (OSError, ValueError, TypeError):
            self.state = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({k: asdict(v) for k, v in self.state.items()}, indent=1), encoding="utf-8")
        except OSError:
            pass

    def _q(self, provider: str) -> ProviderQuota:
        return self.state.setdefault(provider, ProviderQuota())

    # ---------- updates ----------
    def update_from_headers(self, provider: str, headers: Mapping[str, str]) -> None:
        h = {k.lower(): v for k, v in dict(headers).items()}
        with self._lock:
            q = self._q(provider)
            q.used_session += 1
            # Groq style: *-requests is per day, *-tokens is per minute
            q.requests_day_limit = _int(h.get("x-ratelimit-limit-requests")) or _int(h.get("x-ratelimit-limit-requests-day")) or q.requests_day_limit
            rem = h.get("x-ratelimit-remaining-requests", h.get("x-ratelimit-remaining-requests-day"))
            if rem is not None:
                q.requests_day_remaining = _int(rem)
            q.tokens_minute_limit = _int(h.get("x-ratelimit-limit-tokens")) or _int(h.get("x-ratelimit-limit-tokens-minute")) or q.tokens_minute_limit
            trem = h.get("x-ratelimit-remaining-tokens", h.get("x-ratelimit-remaining-tokens-minute"))
            if trem is not None:
                q.tokens_minute_remaining = _int(trem)
                reset = parse_duration(h.get("x-ratelimit-reset-tokens") or h.get("x-ratelimit-reset-tokens-minute"))
                q.tokens_reset_at = time.time() + (reset if reset is not None else 60)
            elif q.requests_day_remaining is not None and rem is not None:
                pass
            if rem is None and trem is None and q.requests_day_remaining is not None:
                q.requests_day_remaining = max(0, q.requests_day_remaining - 1)   # no headers: count locally
            q.updated_at = time.time()
            self._save()

    def mark_429(self, provider: str, retry_after_s: float | None) -> None:
        with self._lock:
            q = self._q(provider)
            q.cooldown_until = time.time() + (retry_after_s if retry_after_s and retry_after_s > 0 else 30.0)
            self._save()

    def disable(self, provider: str, reason: str) -> None:
        with self._lock:
            self._q(provider).disabled_reason = reason
            self._save()

    # ---------- queries ----------
    def available(self, provider: str) -> bool:
        q = self.state.get(provider)
        if q is None:
            return True
        return q.disabled_reason is None and time.time() >= q.cooldown_until

    def why_unavailable(self, provider: str) -> str | None:
        q = self.state.get(provider)
        if q is None:
            return None
        if q.disabled_reason:
            return f"disabled({q.disabled_reason})"
        if time.time() < q.cooldown_until:
            return f"cooldown({int(q.cooldown_until - time.time())}s)"
        return None

    def tokens_minute_remaining(self, provider: str) -> int | None:
        q = self.state.get(provider)
        if q is None or q.tokens_minute_remaining is None:
            return None
        if q.tokens_reset_at is not None and time.time() >= q.tokens_reset_at:
            return q.tokens_minute_limit          # window has reset
        return q.tokens_minute_remaining

    def ok(self, provider: str, est_tokens: int) -> bool:
        """auto-mode rule (03 §3): enough daily requests left AND enough TPM for this prompt."""
        if not self.available(provider):
            return False
        q = self.state.get(provider)
        if q is None:
            return True                            # never called: assume fresh quota
        if q.requests_day_remaining is not None and q.requests_day_remaining <= get_settings().AUTO_MIN_RPD:
            return False
        tpm = self.tokens_minute_remaining(provider)
        if tpm is not None and tpm < est_tokens * 1.2:
            return False
        return True

    def snapshot(self) -> dict:
        out = {}
        for k, q in self.state.items():
            out[k] = {"requests_day_remaining": q.requests_day_remaining, "requests_day_limit": q.requests_day_limit,
                      "tokens_minute_remaining": self.tokens_minute_remaining(k), "tokens_minute_limit": q.tokens_minute_limit,
                      "cooldown_s": max(0, int(q.cooldown_until - time.time())), "disabled": q.disabled_reason,
                      "used_session": q.used_session}
        return out
