from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server
    env: str = "development"
    port: int = 8000

    # Redis / Celery
    redis_url: str = "redis://localhost:6379"

    # File storage
    upload_dir: str = "/tmp/safeguardmedia/uploads"
    output_dir: str = "/tmp/safeguardmedia/outputs"

    # File size limits (MB)
    max_video_size_mb: int = 500
    max_audio_size_mb: int = 200
    max_image_size_mb: int = 50

    # Cleanup
    cleanup_max_age_hours: int = 24
    cleanup_interval_seconds: int = 3600

    # CORS — accepts "*", "https://a.com,https://b.com", or JSON ["a", "b"]
    cors_origins: list[str] = ["*"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> list[str]:
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                import json
                return json.loads(v)
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return [str(v)]

    @property
    def is_production(self) -> bool:
        return self.env == "production"


settings = Settings()
