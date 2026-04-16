"""Shared SMTP email helper."""

from email.message import EmailMessage

import aiosmtplib

from shared.config import settings


async def send_email(*, to: str, subject: str, body: str) -> None:
    """Build and send a plain-text email using the configured SMTP settings.

    Raises on any SMTP error so the caller can handle it.
    """
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user or None,
        password=settings.smtp_password or None,
        start_tls=settings.smtp_tls,
    )
