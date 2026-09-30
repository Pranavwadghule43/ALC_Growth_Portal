import ipaddress
from functools import lru_cache

from pydantic import Field, field_validator
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
    # Maximum Argon2 hash/verify operations running at once (each uses ~64 MiB and 4 lanes).
    # Extra requests wait their turn instead of piling onto the thread pool.
    password_hash_concurrency: int = Field(default=2, ge=1, le=32)
    # Login rate limiting (Phase 4G-B), applied before any password check.
    # Every attempt from one client IP counts toward the IP limit (generous: offices share
    # one NAT address); failed attempts for the same client IP + identifier count toward the
    # pair limit. No account-wide lockout.
    login_ip_limit: int = Field(default=100, ge=1, le=100_000)
    login_ip_window_seconds: int = Field(default=600, ge=1, le=86_400)
    login_pair_failure_limit: int = Field(default=8, ge=1, le=1_000)
    login_pair_failure_window_seconds: int = Field(default=900, ge=1, le=86_400)
    # Max counters kept by the process-local fallback used only while Redis is unreachable.
    login_fallback_max_keys: int = Field(default=10_000, ge=100, le=1_000_000)
    # Comma-separated proxy addresses/CIDRs whose forwarding headers are believed, e.g.
    # "127.0.0.1/32". Empty (default): forwarding headers are never trusted.
    trusted_proxy_cidrs: str = ""
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

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def _valid_proxy_cidrs(cls, value: str) -> str:
        for item in value.split(","):
            if item.strip():
                ipaddress.ip_network(item.strip(), strict=False)  # raises on a bad entry
        return value

    @property
    def trusted_proxy_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        return [
            ipaddress.ip_network(item.strip(), strict=False)
            for item in self.trusted_proxy_cidrs.split(",")
            if item.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()