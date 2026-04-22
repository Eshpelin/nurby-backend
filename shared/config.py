import logging
import secrets

from pydantic_settings import BaseSettings

_logger = logging.getLogger("nurby.config")

_DEFAULT_JWT_SECRET = "change-me-in-production-use-a-real-secret"


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://nurby:nurby_dev@localhost:5433/nurby"
    redis_url: str = "redis://localhost:6379/0"
    mediamtx_api_url: str = "http://localhost:9997"
    mediamtx_rtsp_url: str = "rtsp://localhost:8554"  # target for webcam bridge publishes
    recordings_path: str = "./recordings"
    thumbnails_path: str = "./thumbnails"
    jwt_secret: str = _DEFAULT_JWT_SECRET
    jwt_expiry_hours: int = 24
    cors_origins: str = ""  # comma-separated additional origins

    # Starred-person recap
    recap_ttl_seconds: int = 300
    recap_timeout_seconds: float = 20.0
    recap_default_provider: str = ""  # openai|anthropic|google|ollama. empty = auto

    # SMTP settings for email notifications
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: bool = True

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

# Warn loudly if JWT secret is the insecure default
if settings.jwt_secret == _DEFAULT_JWT_SECRET:
    _generated = secrets.token_urlsafe(32)
    _logger.warning(
        "JWT_SECRET is the insecure default. Generating a random secret for this session. "
        "Set JWT_SECRET in your .env file for persistent tokens. Generated secret (add to .env). JWT_SECRET=%s",
        _generated,
    )
    settings.jwt_secret = _generated

# Warn if SMTP is partially configured
if settings.smtp_host and not settings.smtp_user:
    _logger.warning("SMTP_HOST is set but SMTP_USER is empty. Email sending may fail.")
