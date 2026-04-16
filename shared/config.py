from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://nurby:nurby_dev@localhost:5433/nurby"
    redis_url: str = "redis://localhost:6379/0"
    mediamtx_api_url: str = "http://localhost:9997"
    recordings_path: str = "./recordings"
    thumbnails_path: str = "./thumbnails"
    jwt_secret: str = "change-me-in-production-use-a-real-secret"
    jwt_expiry_hours: int = 24

    # SMTP settings for email notifications
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: bool = True

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
