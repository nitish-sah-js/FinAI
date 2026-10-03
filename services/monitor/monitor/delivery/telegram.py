"""Optional Tier-3 Telegram (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID). Queue drained at ≤ 1 message / 3 s. Never raises."""
from __future__ import annotations

import asyncio
import html
import logging

import httpx

from copilot_common.settings import get_settings

log = logging.getLogger("monitor.telegram")
STATUS = {"state": "disabled", "last_error": None, "sent": 0}
MIN_GAP_S = 3.0
_queue: asyncio.Queue | None = None


def enabled() -> bool:
    s = get_settings()
    return bool(s.TELEGRAM_BOT_TOKEN and s.TELEGRAM_CHAT_ID)


def format_alert(alert) -> str:
    return (f"<b>Tier {alert.tier} alert</b>\n{html.escape(alert.headline)}\n<i>{html.escape(alert.reason)}</i>\n"
            f"<a href=\"{html.escape(alert.deeplink)}\">Analyze</a>")


async def _post(text: str) -> None:
    s = get_settings()
    url = f"https://api.telegram.org/bot{s.TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(url, json={"chat_id": s.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML",
                                    "disable_web_page_preview": True})
        r.raise_for_status()


def enqueue(text: str) -> bool:
    if not enabled() or _queue is None:
        return False
    _queue.put_nowait(text)
    return True


async def worker() -> None:
    """Started by the service lifespan."""
    global _queue
    _queue = asyncio.Queue(maxsize=100)
    STATUS["state"] = "ok" if enabled() else "disabled"
    while True:
        text = await _queue.get()
        try:
            await _post(text)
            STATUS.update(state="ok", last_error=None, sent=STATUS["sent"] + 1)
        except Exception as e:  # noqa: BLE001
            STATUS.update(state="degraded", last_error=type(e).__name__)
            log.error("telegram delivery failed: %s", type(e).__name__)
        await asyncio.sleep(MIN_GAP_S)
