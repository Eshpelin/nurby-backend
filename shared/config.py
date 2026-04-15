from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://nurby:nurby_dev@localhost:5432/nurby"
    redis_url: str = "redis://localhost:6379/0"
    mediamtx_api_url: str = "http://localhost:9997"
    recordings_path: str = "./recordings"
    thumbnails_path: str = "./thumbnails"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
