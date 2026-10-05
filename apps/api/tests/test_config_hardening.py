"""Phase 5B-1: production configuration hardening.

``APP_ENV`` accepts only development / test / production. With ``APP_ENV=production`` the
critical configuration is validated before the app starts; unsafe, default, placeholder or
missing values stop startup with a ``ConfigurationError`` that names keys but never values.
Development and test keep their local defaults.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import (
    ConfigurationError,
    Settings,
    production_config_problems,
    settings,
    validate_settings,
)

API_DIR = Path(__file__).resolve().parents[1]

# Realistic, non-placeholder values. None of them is a real credential.
STRONG_SECRET = "Qm7tL2vX9pK4wR8zN3cH6jF1sD5gA0eUyB"
DB_PASSWORD = "Db-Pw-9f3K2mQ7xLr"
S3_SECRET = "S3-Secret-7hQ2pL9xW4kZ"
PRODUCTION = {
    "app_env": "production",
    "secret_key": STRONG_SECRET,
    "cookie_secure": True,
    "database_url": f"postgresql+asyncpg://alc_app:{DB_PASSWORD}@127.0.0.1:5432/alc_growth",
    "redis_url": "redis://127.0.0.1:6379/0",
    "trusted_proxy_cidrs": "127.0.0.1/32",
    "cors_origins": "",
    "storage_backend": "s3",
    "s3_endpoint_url": "http://127.0.0.1:9000",
    "s3_bucket": "alc-evidence",
    "s3_access_key": "alc-evidence-writer",
    "s3_secret_key": S3_SECRET,
}
# Environment variables that would otherwise count as explicit configuration.
CONFIG_ENV = ("APP_ENV", "SECRET_KEY", "DATABASE_URL", "REDIS_URL", "TRUSTED_PROXY_CIDRS",
              "CORS_ORIGINS", "COOKIE_SECURE", "STORAGE_BACKEND", "S3_BUCKET", "S3_ACCESS_KEY",
              "S3_SECRET_KEY", "S3_ENDPOINT_URL", "LOGIN_PAIR_WINDOW_SECONDS")


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for key in CONFIG_ENV:
        monkeypatch.delenv(key, raising=False)


def build(**overrides) -> Settings:
    values = {**PRODUCTION, **overrides}
    return Settings(_env_file=None, **{k: v for k, v in values.items() if v is not ...})


def problems(**overrides) -> list[str]:
    return production_config_problems(build(**overrides), environ={})


def assert_rejected(expected: str, **overrides) -> None:
    found = problems(**overrides)
    assert any(expected in p for p in found), found
    with pytest.raises(ConfigurationError):
        validate_settings(build(**overrides), environ={})


# --------------------------------------------------------------------------- #
# Valid configurations
# --------------------------------------------------------------------------- #
def test_development_defaults_stay_convenient():
    dev = Settings(_env_file=None)
    assert dev.app_env == "development"
    assert dev.cookie_secure is False
    assert dev.allowed_origins == ["http://localhost:5173"]
    assert dev.trusted_proxy_cidrs == ""
    assert validate_settings(dev, environ={}) is dev  # no production rules in development


def test_development_accepts_local_values_production_would_reject():
    dev = Settings(_env_file=None, app_env="development", cors_origins="http://localhost:5173",
                   cookie_secure=False, storage_backend="local", s3_secret_key="minioadmin",
                   database_url="postgresql+asyncpg://alc:alc_dev_password@127.0.0.1:55432/alc")
    assert validate_settings(dev, environ={}) is dev


def test_test_environment_initialises():
    assert settings.app_env == "test"  # conftest
    assert validate_settings(Settings(_env_file=None, app_env="test"), environ={})


def test_fully_configured_production_is_valid():
    assert problems() == []
    assert validate_settings(build(), environ={}).app_env == "production"


def test_production_accepts_https_cors_origin_and_aws_s3():
    assert problems(cors_origins="https://portal.example.org", s3_endpoint_url="") == []


# --------------------------------------------------------------------------- #
# APP_ENV / STORAGE_BACKEND (every environment)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["prodution", "Production", "staging", "prod", ""])
def test_unknown_app_env_is_rejected(value):
    allowed = "APP_ENV must be one of: development, test, production"
    with pytest.raises(ValidationError, match=allowed):
        Settings(_env_file=None, app_env=value)


def test_unknown_storage_backend_is_rejected():
    with pytest.raises(ValidationError, match="STORAGE_BACKEND must be one of: s3, local"):
        Settings(_env_file=None, storage_backend="minio")


# --------------------------------------------------------------------------- #
# SECRET_KEY
# --------------------------------------------------------------------------- #
def test_production_requires_secret_key():
    assert_rejected("SECRET_KEY must be set explicitly", secret_key=...)


@pytest.mark.parametrize("value", [
    "development-only-change-me-please-32-chars",
    "replace-with-a-long-random-secret-at-least-32-characters",
    "test-secret-key-that-is-long-enough-123456",
    "Qm7tL2vX9pK4-CHANGEME-wR8zN3cH6jF1sD5",
    "an-example-secret-value-for-docs-only-0123",
])
def test_production_rejects_placeholder_secret_key(value):
    assert_rejected("SECRET_KEY is a default, example or placeholder value", secret_key=value)


def test_production_rejects_blank_short_or_weak_secret_key():
    assert_rejected("SECRET_KEY must not be blank", secret_key="   ")
    assert_rejected("SECRET_KEY must be at least 32 characters", secret_key=STRONG_SECRET[:31])
    assert_rejected("SECRET_KEY is too weak", secret_key="ab" * 20)


def test_production_template_never_starts_unedited():
    """``.env.production.example`` must fail validation until its placeholders are replaced."""
    template = Settings(_env_file=API_DIR.parents[1] / ".env.production.example")
    assert template.app_env == "production" and template.trusted_proxy_cidrs == "127.0.0.1/32"
    found = production_config_problems(template, environ={})
    for expected in ("SECRET_KEY is a default, example or placeholder value",
                     "DATABASE_URL uses a default, example or placeholder password",
                     "S3_ACCESS_KEY is a default, example or placeholder value",
                     "S3_SECRET_KEY is a default, example or placeholder value"):
        assert expected in found, found
    assert len(found) == 4  # everything else in the template is already production-safe


# --------------------------------------------------------------------------- #
# Cookies and token lifetimes
# --------------------------------------------------------------------------- #
def test_production_rejects_insecure_cookies():
    assert_rejected("COOKIE_SECURE must be true", cookie_secure=False)
    assert_rejected("COOKIE_SECURE must be true", cookie_secure=...)  # default is false


def test_production_rejects_unsafe_token_lifetimes():
    assert_rejected("ACCESS_TOKEN_MINUTES must be between 1 and 60", access_token_minutes=0)
    assert_rejected("ACCESS_TOKEN_MINUTES must be between 1 and 60", access_token_minutes=600)
    assert_rejected("REFRESH_TOKEN_DAYS must be between 1 and 30", refresh_token_days=365)


# --------------------------------------------------------------------------- #
# Database / Redis
# --------------------------------------------------------------------------- #
def test_production_database_url():
    assert_rejected("DATABASE_URL must be set explicitly", database_url=...)
    assert_rejected("postgresql+asyncpg", database_url="sqlite+aiosqlite:///./prod.db")
    assert_rejected("DATABASE_URL uses a default, example or placeholder password",
                    database_url="postgresql+asyncpg://alc:alc_dev_password@127.0.0.1:5432/alc")
    assert_rejected("DATABASE_URL is not a valid database URL", database_url="not a url")
    # The port is configuration, not policy: a private 5432 and a local 55432 both pass.
    assert problems(database_url=f"postgresql+asyncpg://a:{DB_PASSWORD}@10.0.0.5:5432/db") == []


def test_production_requires_valid_redis_url():
    assert_rejected("REDIS_URL must be set explicitly", redis_url=...)
    assert_rejected("REDIS_URL must be a redis://, rediss:// or unix:// URL",
                    redis_url="http://127.0.0.1:6379")
    assert problems(redis_url="rediss://cache.internal:6380/0") == []
    assert problems(redis_url="unix:///run/redis/redis.sock") == []


# --------------------------------------------------------------------------- #
# Trusted proxy / CORS
# --------------------------------------------------------------------------- #
def test_production_requires_deliberate_trusted_proxy():
    assert_rejected("TRUSTED_PROXY_CIDRS must list the reverse proxy", trusted_proxy_cidrs=...)
    assert_rejected("TRUSTED_PROXY_CIDRS must list the reverse proxy", trusted_proxy_cidrs="")
    for broad in ("0.0.0.0/0", "::/0", "10.0.0.0/7", "127.0.0.1/32, 0.0.0.0/0"):
        assert_rejected("too broad", trusted_proxy_cidrs=broad)
    assert problems(trusted_proxy_cidrs="127.0.0.1/32, ::1/128") == []


def test_invalid_trusted_proxy_cidr_is_rejected():
    with pytest.raises(ValidationError):
        build(trusted_proxy_cidrs="127.0.0.1/32, not-a-network")


@pytest.mark.parametrize("origins, expected", [
    ("*", "must not contain a wildcard"),
    ("https://*.example.org", "must not contain a wildcard"),
    ("http://portal.example.org", "must be https:// origins"),
    ("https://localhost:5173", "must not include localhost"),
    ("https://127.0.0.1", "must not include localhost"),
    ("https://portal.example.org/app", "bare origins without a path"),
])
def test_production_rejects_unsafe_cors_origins(origins, expected):
    assert_rejected(expected, cors_origins=origins)


# --------------------------------------------------------------------------- #
# Object storage
# --------------------------------------------------------------------------- #
def test_production_requires_private_s3_storage():
    assert_rejected("STORAGE_BACKEND must be s3", storage_backend="local")
    assert_rejected("S3_BUCKET must be set explicitly", s3_bucket=...)
    assert_rejected("S3_BUCKET must be set explicitly", s3_bucket="  ")
    assert_rejected("S3_ACCESS_KEY must be set explicitly", s3_access_key=...)
    assert_rejected("S3_SECRET_KEY must be set explicitly", s3_secret_key=...)
    assert_rejected("S3_ACCESS_KEY is a default", s3_access_key="minioadmin")
    assert_rejected("S3_SECRET_KEY is a default", s3_secret_key="minioadmin")
    assert_rejected("S3_SECRET_KEY must be at least 16 characters", s3_secret_key="Short-S3-Pw1")
    assert_rejected("S3_ENDPOINT_URL must be an http(s):// URL", s3_endpoint_url="ftp://minio")
    assert_rejected("S3_PRESIGN_SECONDS must be between 1 and 3600", s3_presign_seconds=86_400)


# --------------------------------------------------------------------------- #
# Login limiter variables
# --------------------------------------------------------------------------- #
def test_production_rejects_misspelled_login_window_variable():
    found = production_config_problems(build(), environ={"LOGIN_PAIR_WINDOW_SECONDS": "900"})
    assert found == ["LOGIN_PAIR_WINDOW_SECONDS is not a setting; use "
                     "LOGIN_PAIR_FAILURE_WINDOW_SECONDS"]


def test_login_pair_failure_window_is_read_from_its_documented_name(monkeypatch):
    monkeypatch.setenv("LOGIN_PAIR_FAILURE_WINDOW_SECONDS", "1200")
    assert Settings(_env_file=None).login_pair_failure_window_seconds == 1200


# --------------------------------------------------------------------------- #
# Error messages never reveal secret values
# --------------------------------------------------------------------------- #
def test_errors_name_keys_but_never_print_secret_values():
    leaky = {
        "secret_key": "Sh0rt-But-Real-Secret!",
        "s3_secret_key": "S3-short-Pw!",
        "s3_access_key": "minioadmin",
        "database_url": "postgresql+asyncpg://alc:alc_dev_password@db:5432/alc",
        "redis_url": "redis://:R3dis-Pass-word-x@cache:6379/0",
        "cookie_secure": False,
        "trusted_proxy_cidrs": "0.0.0.0/0",
        "cors_origins": "*",
    }
    with pytest.raises(ConfigurationError) as caught:
        validate_settings(build(**leaky), environ={})
    message = str(caught.value)
    for key in ("SECRET_KEY", "S3_SECRET_KEY", "S3_ACCESS_KEY", "DATABASE_URL",
                "COOKIE_SECURE", "TRUSTED_PROXY_CIDRS", "CORS_ORIGINS"):
        assert key in message
    for value in ("Sh0rt-But-Real-Secret!", "S3-short-Pw!", "minioadmin", "alc_dev_password",
                  "R3dis-Pass-word-x"):
        assert value not in message


# --------------------------------------------------------------------------- #
# Startup (a fresh interpreter, exactly as Uvicorn would import the app)
# --------------------------------------------------------------------------- #
PROBE = """
import asyncio, httpx
from app.main import app
async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        for path in ("/api/health", "/docs", "/redoc", "/openapi.json"):
            r = await c.get(path)
            print(path, r.status_code, r.headers.get("strict-transport-security", "-"))
asyncio.run(main())
"""


def start(env: dict) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.items() if k not in CONFIG_ENV}
    return subprocess.run([sys.executable, "-c", PROBE], cwd=API_DIR, env={**clean, **env},
                          capture_output=True, text=True, timeout=120)


def production_env(**overrides) -> dict:
    env = {
        "APP_ENV": "production", "SECRET_KEY": STRONG_SECRET, "COOKIE_SECURE": "true",
        "DATABASE_URL": PRODUCTION["database_url"], "REDIS_URL": PRODUCTION["redis_url"],
        "TRUSTED_PROXY_CIDRS": "127.0.0.1/32", "CORS_ORIGINS": "", "STORAGE_BACKEND": "s3",
        "S3_ENDPOINT_URL": "http://127.0.0.1:9000", "S3_BUCKET": "alc-evidence",
        "S3_ACCESS_KEY": "alc-evidence-writer", "S3_SECRET_KEY": S3_SECRET,
    }
    env.update(overrides)
    return {k: v for k, v in env.items() if v is not None}


def test_safe_production_starts_without_docs_and_with_hsts():
    result = start(production_env())
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith("/api/health 200 max-age=")
    assert [line.split()[:2] for line in lines[1:]] == [
        ["/docs", "404"], ["/redoc", "404"], ["/openapi.json", "404"]]


def test_unsafe_production_refuses_to_start_without_leaking_secrets():
    result = start(production_env(SECRET_KEY="development-only-change-me-please-32-chars",
                                  S3_SECRET_KEY="minioadmin", COOKIE_SECURE="false"))
    assert result.returncode != 0
    assert "ConfigurationError" in result.stderr
    assert "SECRET_KEY" in result.stderr and "COOKIE_SECURE" in result.stderr
    assert "development-only-change-me-please-32-chars" not in result.stderr
    assert "minioadmin" not in result.stderr
    assert STRONG_SECRET not in result.stderr and DB_PASSWORD not in result.stderr


def test_app_env_typo_refuses_to_start():
    result = start(production_env(APP_ENV="prodution"))
    assert result.returncode != 0
    assert "APP_ENV must be one of: development, test, production" in result.stderr


def test_development_still_starts_with_docs():
    result = start({"APP_ENV": "development", "DATABASE_URL": "sqlite+aiosqlite://"})
    assert result.returncode == 0, result.stderr
    assert "/docs 200" in result.stdout and "/openapi.json 200" in result.stdout
