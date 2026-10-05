"""Application settings, read from environment variables (and ``.env`` files).

``APP_ENV`` must be one of ``development``, ``test`` or ``production``; anything else stops
startup (a typo never silently runs as development). With ``APP_ENV=production`` the whole
critical configuration is validated before the app is created (``validate_settings``):
unsafe, default, placeholder or missing values make startup fail with a
``ConfigurationError`` that names the offending keys but never prints their values.
Development and test keep their convenient local defaults.
"""
import ipaddress
import os
from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

APP_ENVIRONMENTS = ("development", "test", "production")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
STORAGE_BACKENDS = ("s3", "local")


class ConfigurationError(RuntimeError):
    """Startup configuration is invalid or unsafe. Messages name keys, never values."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../../.env", ".env"), extra="ignore")
    app_env: str = "development"
    # Minimum level written to the log. INFO in every environment unless set explicitly;
    # DEBUG is never enabled automatically.
    log_level: str = "INFO"
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

    @field_validator("app_env")
    @classmethod
    def _known_app_env(cls, value: str) -> str:
        value = value.strip()
        if value not in APP_ENVIRONMENTS:
            raise ValueError(f"APP_ENV must be one of: {', '.join(APP_ENVIRONMENTS)}")
        return value

    @field_validator("log_level")
    @classmethod
    def _known_log_level(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL must be one of: {', '.join(LOG_LEVELS)}")
        return value

    @field_validator("storage_backend")
    @classmethod
    def _known_storage_backend(cls, value: str) -> str:
        value = value.strip()
        if value not in STORAGE_BACKENDS:
            raise ValueError(f"STORAGE_BACKEND must be one of: {', '.join(STORAGE_BACKENDS)}")
        return value

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


# --------------------------------------------------------------------------- #
# Production validation (APP_ENV=production)
# --------------------------------------------------------------------------- #
MIN_SECRET_KEY_LENGTH = 32
MIN_SECRET_KEY_DISTINCT_CHARS = 10
MIN_S3_SECRET_KEY_LENGTH = 16
# Values shipped as development defaults or examples. Production never accepts them.
KNOWN_DEFAULT_SECRETS = frozenset({
    "development-only-change-me-please-32-chars",  # SECRET_KEY default
    "replace-with-a-long-random-secret-at-least-32-characters",  # .env.example
    "test-secret-key-that-is-long-enough-123456",  # test suite
    "minioadmin",  # MinIO / S3 default credentials
    "alc_dev_password",  # docker-compose / default DATABASE_URL password
})
# Case-insensitive fragments that mark a value as a placeholder rather than a real secret.
PLACEHOLDER_MARKERS = (
    "change-me", "changeme", "change_me", "replace-with", "replace_me", "replaceme",
    "development-only", "dev-only", "example", "placeholder", "dummy", "your-secret",
    "your_secret", "test-secret", "insecure", "minioadmin",
)
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})
REDIS_SCHEMES = frozenset({"redis", "rediss", "unix"})
# Trusting a range broader than this would let almost any client forge its IP.
MIN_TRUSTED_PREFIX = {4: 8, 6: 32}
MAX_ACCESS_TOKEN_MINUTES = 60
MAX_REFRESH_TOKEN_DAYS = 30
MAX_S3_PRESIGN_SECONDS = 3600
# Known misspelling of LOGIN_PAIR_FAILURE_WINDOW_SECONDS; it would be silently ignored.
MISSPELLED_ENV_KEYS = {"LOGIN_PAIR_WINDOW_SECONDS": "LOGIN_PAIR_FAILURE_WINDOW_SECONDS"}


def _placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    if "<" in lowered or ">" in lowered:  # unfilled template value, e.g. <db-password>
        return True
    return lowered in KNOWN_DEFAULT_SECRETS or any(m in lowered for m in PLACEHOLDER_MARKERS)


def _secret_problems(key: str, value: str, explicit: bool, min_length: int) -> list[str]:
    """Problems with one secret, naming only the key (never the value)."""
    if not explicit:
        return [f"{key} must be set explicitly"]
    if not value.strip():
        return [f"{key} must not be blank"]
    if _placeholder(value):
        return [f"{key} is a default, example or placeholder value"]
    if len(value) < min_length:
        return [f"{key} must be at least {min_length} characters"]
    return []


def _database_problems(settings: Settings, explicit: bool) -> list[str]:
    if not explicit:
        return ["DATABASE_URL must be set explicitly"]
    try:
        url = make_url(settings.database_url)
    except ArgumentError:
        return ["DATABASE_URL is not a valid database URL"]
    if url.drivername != "postgresql+asyncpg":
        return ["DATABASE_URL must use the postgresql+asyncpg:// driver"]
    if url.password is not None and _placeholder(str(url.password)):
        return ["DATABASE_URL uses a default, example or placeholder password"]
    return []


def _redis_problems(settings: Settings, explicit: bool) -> list[str]:
    if not explicit:
        return ["REDIS_URL must be set explicitly (Redis backs login rate limiting)"]
    parts = urlsplit(settings.redis_url.strip())
    if parts.scheme not in REDIS_SCHEMES or not (parts.hostname or parts.path):
        return ["REDIS_URL must be a redis://, rediss:// or unix:// URL"]
    if parts.password and _placeholder(parts.password):
        return ["REDIS_URL uses a default, example or placeholder password"]
    return []


def _trusted_proxy_problems(settings: Settings, explicit: bool) -> list[str]:
    networks = settings.trusted_proxy_networks
    if not explicit or not networks:
        return [
            "TRUSTED_PROXY_CIDRS must list the reverse proxy address(es), "
            "e.g. 127.0.0.1/32 when the proxy runs on the same server"
        ]
    if any(n.prefixlen < MIN_TRUSTED_PREFIX[n.version] for n in networks):
        return [
            "TRUSTED_PROXY_CIDRS contains a range that is too broad "
            "(each range must be /8 or narrower for IPv4, /32 or narrower for IPv6)"
        ]
    return []


def _cors_problems(settings: Settings) -> list[str]:
    """Production is same-origin, so CORS_ORIGINS may be empty. Any origin listed must be an
    exact https origin: no wildcard (credentials are sent), no localhost, no path."""
    problems = []
    for origin in settings.allowed_origins:
        if "*" in origin:
            problems.append("CORS_ORIGINS must not contain a wildcard")
            continue
        parts = urlsplit(origin)
        if parts.scheme != "https" or not parts.hostname:
            problems.append("CORS_ORIGINS entries must be https:// origins")
        elif parts.hostname in LOCAL_HOSTS or parts.hostname.endswith(".localhost"):
            problems.append("CORS_ORIGINS must not include localhost / loopback origins")
        elif parts.path not in ("", "/") or parts.query or parts.fragment:
            problems.append("CORS_ORIGINS entries must be bare origins without a path")
    return sorted(set(problems))


def _storage_problems(settings: Settings, fields: set[str]) -> list[str]:
    if settings.storage_backend != "s3":
        return ["STORAGE_BACKEND must be s3 in production (local disk storage is development-only)"]
    problems = []
    if "s3_bucket" not in fields or not settings.s3_bucket.strip():
        problems.append("S3_BUCKET must be set explicitly")
    problems += _secret_problems("S3_ACCESS_KEY", settings.s3_access_key,
                                 "s3_access_key" in fields, 1)
    problems += _secret_problems("S3_SECRET_KEY", settings.s3_secret_key,
                                 "s3_secret_key" in fields, MIN_S3_SECRET_KEY_LENGTH)
    if settings.s3_endpoint_url:
        endpoint = urlsplit(settings.s3_endpoint_url.strip())
        if endpoint.scheme not in ("http", "https") or not endpoint.hostname:
            problems.append("S3_ENDPOINT_URL must be an http(s):// URL, or empty for AWS S3")
    if not 1 <= settings.s3_presign_seconds <= MAX_S3_PRESIGN_SECONDS:
        problems.append(f"S3_PRESIGN_SECONDS must be between 1 and {MAX_S3_PRESIGN_SECONDS}")
    return problems


def production_config_problems(settings: Settings, environ=None) -> list[str]:
    """Every reason ``settings`` is unsafe for production (empty when it is safe).

    Keys count as configured only when they were set explicitly (environment variable,
    ``.env`` file or constructor argument), so a development default can never pass as a
    production value. Messages name keys and rules, never the values themselves."""
    environ = os.environ if environ is None else environ
    fields = set(settings.model_fields_set)
    problems = _secret_problems("SECRET_KEY", settings.secret_key, "secret_key" in fields,
                                MIN_SECRET_KEY_LENGTH)
    if not problems and len(set(settings.secret_key)) < MIN_SECRET_KEY_DISTINCT_CHARS:
        problems.append("SECRET_KEY is too weak (too few distinct characters)")
    if not settings.cookie_secure:
        problems.append("COOKIE_SECURE must be true (auth cookies are HTTPS-only in production)")
    if not 1 <= settings.access_token_minutes <= MAX_ACCESS_TOKEN_MINUTES:
        problems.append(f"ACCESS_TOKEN_MINUTES must be between 1 and {MAX_ACCESS_TOKEN_MINUTES}")
    if not 1 <= settings.refresh_token_days <= MAX_REFRESH_TOKEN_DAYS:
        problems.append(f"REFRESH_TOKEN_DAYS must be between 1 and {MAX_REFRESH_TOKEN_DAYS}")
    problems += _database_problems(settings, "database_url" in fields)
    problems += _redis_problems(settings, "redis_url" in fields)
    problems += _trusted_proxy_problems(settings, "trusted_proxy_cidrs" in fields)
    problems += _cors_problems(settings)
    problems += _storage_problems(settings, fields)
    for wrong, right in MISSPELLED_ENV_KEYS.items():
        if wrong in environ:
            problems.append(f"{wrong} is not a setting; use {right}")
    return problems


def validate_settings(settings: Settings, environ=None) -> Settings:
    """Raise ``ConfigurationError`` when ``APP_ENV=production`` and anything is unsafe."""
    if settings.app_env == "production":
        problems = production_config_problems(settings, environ)
        if problems:
            raise ConfigurationError(
                "Refusing to start: unsafe production configuration (APP_ENV=production):\n"
                + "\n".join(f"  - {problem}" for problem in problems)
            )
    return settings


@lru_cache
def get_settings() -> Settings:
    return validate_settings(Settings())


settings = get_settings()