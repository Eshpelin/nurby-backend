"""Shared SMTP email helper.

Config resolution order: the ``smtp_config`` app setting (saved from the
Settings UI, password Fernet-sealed) wins over the SMTP_* environment
variables, so email can be configured in-app with no container restart.
The env vars remain a valid headless path.
"""

from email.message import EmailMessage

import aiosmtplib

from shared.config import settings


async def resolve_smtp() -> dict:
    """Effective SMTP config as a plain dict with a decrypted password.

    Keys: host, port, user, password, from_addr, tls, source. ``source``
    is "db" when the in-app config is set, "env" when only environment
    variables are, and "unconfigured" when neither names a host.
    """
    from shared.app_settings import get_setting

    cfg = None
    try:
        cfg = await get_setting("smtp_config")
    except Exception:
        cfg = None
    if isinstance(cfg, dict) and (cfg.get("host") or "").strip():
        password = ""
        enc = cfg.get("password_enc") or ""
        if enc:
            from shared.crypto import InvalidToken, decrypt_secret

            try:
                password = decrypt_secret(enc.encode("utf-8"))
            except (InvalidToken, ValueError):
                password = ""
        return {
            "host": cfg.get("host", "").strip(),
            "port": int(cfg.get("port") or 587),
            "user": (cfg.get("user") or "").strip(),
            "password": password,
            "from_addr": (cfg.get("from") or cfg.get("user") or "").strip(),
            "tls": bool(cfg.get("tls", True)),
            "source": "db",
        }
    return {
        "host": settings.smtp_host or "",
        "port": settings.smtp_port,
        "user": settings.smtp_user or "",
        "password": settings.smtp_password or "",
        "from_addr": settings.smtp_from or settings.smtp_user or "",
        "tls": settings.smtp_tls,
        "source": "env" if settings.smtp_host else "unconfigured",
    }


async def send_email(*, to: str, subject: str, body: str) -> None:
    """Build and send a plain-text email using the resolved SMTP settings.

    Raises on any SMTP error (including unconfigured SMTP) so the caller
    can handle it.
    """
    cfg = await resolve_smtp()
    if not cfg["host"]:
        raise RuntimeError(
            "SMTP is not configured. Set it up in Settings -> Email alerts."
        )
    msg = EmailMessage()
    msg["From"] = cfg["from_addr"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    await aiosmtplib.send(
        msg,
        hostname=cfg["host"],
        port=cfg["port"],
        username=cfg["user"] or None,
        password=cfg["password"] or None,
        start_tls=cfg["tls"],
    )
