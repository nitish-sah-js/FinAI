"""Cooldown + cross-kind merge (11 §5).

decide() returns one of:
  ("new", None)              broadcast a new alert
  ("update", alert_id)       inside the window, same or lower tier: update the stored alert, no re-broadcast
  ("escalate", alert_id)     inside the window, higher tier: re-broadcast with "Escalated:" (same alert_id)
  ("merge", alert_id)        price_z ↔ news_burst on the same ticker within merge_window: fold into that alert
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from copilot_common.service_base import now_utc

MERGE_KINDS = {"price_z", "news_burst"}


@dataclass
class Active:
    alert_id: str
    kind: str
    tier: int
    impact: float
    tickers: list[str]
    created: datetime
    expires: datetime


class CooldownManager:
    def __init__(self, windows_min: dict[str, int], merge_window_min: int = 10):
        self.windows = windows_min
        self.merge_window = timedelta(minutes=merge_window_min)
        self.active: dict[str, Active] = {}

    def _purge(self, now: datetime) -> None:
        for k in [k for k, v in self.active.items() if v.expires <= now]:
            del self.active[k]

    def decide(self, key: str, kind: str, tickers: list[str], tier: int, now: datetime | None = None
               ) -> tuple[str, str | None]:
        now = now or now_utc()
        self._purge(now)
        cur = self.active.get(key)
        if cur:
            return ("escalate" if tier > cur.tier else "update"), cur.alert_id
        if kind in MERGE_KINDS:
            for k, v in self.active.items():
                if (v.kind in MERGE_KINDS and v.kind != kind and set(v.tickers) & set(tickers)
                        and now - v.created <= self.merge_window):
                    return "merge", v.alert_id
        return "new", None

    def record(self, key: str, alert_id: str, kind: str, tier: int, impact: float, tickers: list[str],
               now: datetime | None = None) -> None:
        now = now or now_utc()
        prev = self.active.get(key)
        self.active[key] = Active(alert_id, kind, max(tier, prev.tier if prev else 0), impact, tickers,
                                  prev.created if prev else now,
                                  now + timedelta(minutes=self.windows.get(kind, 30)) if not prev else prev.expires)

    def key_of(self, alert_id: str) -> str | None:
        return next((k for k, v in self.active.items() if v.alert_id == alert_id), None)

    def snapshot(self) -> list[dict]:
        return [{"cooldown_key": k, "alert_id": v.alert_id, "tier": v.tier, "expires": v.expires.isoformat()}
                for k, v in self.active.items()]
