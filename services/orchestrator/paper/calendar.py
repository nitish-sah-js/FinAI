"""Indian equity trading days for paper-trading marks: the exchange_calendars XBOM calendar (BSE; NSE and BSE share
the same trading holidays). The library publishes holidays only up to a horizon (2026-12-31 in v4.13). Past that,
nothing is guessed: weekdays count as trading days and the result says the calendar is unknown for that date.
"""
from __future__ import annotations

import logging
from datetime import date
from functools import lru_cache

log = logging.getLogger("paper.calendar")


@lru_cache(maxsize=1)
def _xbom():
    try:
        import exchange_calendars as xc
        return xc.get_calendar("XBOM")
    except Exception as e:  # noqa: BLE001  library missing: weekday rule, flagged
        log.warning("exchange_calendars unavailable (%s): NSE holidays not applied", e)
        return None


def trading_day(d: date) -> tuple[bool, str]:
    """(is a trading day, source). source: 'XBOM' | 'weekday-only (calendar ends YYYY-MM-DD)' | 'weekday-only (no calendar)'."""
    if d.weekday() >= 5:
        return False, "weekend"
    cal = _xbom()
    if cal is None:
        return True, "weekday-only (no calendar)"
    last = cal.last_session.date()
    if not (cal.first_session.date() <= d <= last):
        return True, f"weekday-only (calendar ends {last.isoformat()})"
    return bool(cal.is_session(d.isoformat())), "XBOM"


def is_trading_day(d: date) -> bool:
    return trading_day(d)[0]
