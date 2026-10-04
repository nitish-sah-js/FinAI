"""Mark job (12 §B3): every 15 min 09:15–15:30 IST on trading days, plus 15:35 IST after the close.
Trading days come from the exchange calendar (paper/calendar.py, XBOM = NSE/BSE holidays); no marks on holidays."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
log = logging.getLogger("paper.scheduler")

from .calendar import is_trading_day  # noqa: E402


def should_mark(now: datetime) -> bool:
    now = now.astimezone(IST)
    if not is_trading_day(now.date()):          # weekend or exchange holiday
        return False
    hm = (now.hour, now.minute)
    if hm == (15, 35):
        return True
    return (9, 15) <= hm <= (15, 30) and now.minute % 15 == 0


async def run_scheduler(poll_s: float = 30.0) -> None:
    """Started from the orchestrator lifespan. One mark per qualifying minute; errors are logged, never fatal."""
    from .db import connect
    from .router import mark_positions
    last = None
    while True:
        now = datetime.now(IST).replace(second=0, microsecond=0)
        if should_mark(now) and now != last:
            last = now
            try:
                db = await connect()
                try:
                    marks = await mark_positions(db)
                    log.info("paper marks at %s: %d", now.isoformat(), len(marks))
                finally:
                    await db.close()
            except Exception as e:  # noqa: BLE001
                log.warning("paper mark failed: %s", e)
        await asyncio.sleep(poll_s)
