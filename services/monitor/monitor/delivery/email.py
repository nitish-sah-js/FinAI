"""Optional Tier-3 email (Gmail SMTP_SSL + app password from .env: SMTP_USER, SMTP_APP_PASSWORD, ALERT_EMAIL_TO).
Disabled unless all three are set. Never raises; failures are recorded for /health."""
from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from copilot_common.settings import get_settings

log = logging.getLogger("monitor.email")
STATUS = {"state": "disabled", "last_error": None}


def enabled() -> bool:
    s = get_settings()
    return bool(s.SMTP_USER and s.SMTP_APP_PASSWORD and s.ALERT_EMAIL_TO)


def _send(subject: str, body: str) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg.set_content(body)
    msg["Subject"], msg["From"], msg["To"] = subject, s.SMTP_USER, s.ALERT_EMAIL_TO
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
        server.login(s.SMTP_USER, s.SMTP_APP_PASSWORD)
        server.send_message(msg)


async def send_email(subject: str, body: str) -> bool:
    if not enabled():
        STATUS["state"] = "disabled"
        return False
    try:
        await asyncio.to_thread(_send, subject, body)
        STATUS.update(state="ok", last_error=None)
        return True
    except Exception as e:  # noqa: BLE001
        STATUS.update(state="degraded", last_error=f"{type(e).__name__}: {e}"[:200])
        log.error("email delivery failed: %s", type(e).__name__)
        return False
