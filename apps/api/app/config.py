from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../../.env", ".env"), extra="ignore")
    app_env: str = "development"
    secret_key: str = "development-only-change-me-please-32-chars"
    database_url: str = "postgresql+asyncpg://alc:alc_dev_password@localhost:5432/alc_growth"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: str = "http://localhost:5173"
    cookie_secure: bool = False
    access_token_minutes: int = 15
    refresh_token_days: int = 14
    max_upload_files: int = 10
    max_upload_bytes: int = 10 * 1024 * 1024
    storage_backend: str = "s3"
    local_storage_path: str = "../../.local/evidence"
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_bucket: str = "alc-evidence"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_presign_seconds: int = 300

    @property
    def allowed_origins(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
