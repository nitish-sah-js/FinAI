import smtplib
import asyncio
from email.message import EmailMessage
from copilot_common import settings
import logging

logger = logging.getLogger("monitor.email")

async def send_email(subject: str, body: str):
    if not settings.smtp_user or not settings.smtp_app_password or not settings.alert_email_to:
        return

    def _send():
        try:
            msg = EmailMessage()
            msg.set_content(body)
            msg['Subject'] = subject
            msg['From'] = settings.smtp_user
            msg['To'] = settings.alert_email_to

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(settings.smtp_user, settings.smtp_app_password)
                server.send_message(msg)
        except Exception as e:
            logger.error(f"Email delivery failed: {e}")

    # Run blocking SMTP calls in a background thread so we don't block the async event loop
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _send)
