"""Phase 5B: bounded S3/MinIO connection, read and retry behaviour."""

import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.storage.s3 import S3StorageService


def test_s3_reliability_defaults_are_bounded():
    config = Settings(_env_file=None)

    assert config.s3_connect_timeout_seconds == 1
    assert config.s3_read_timeout_seconds == 2
    assert config.s3_max_attempts == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("s3_connect_timeout_seconds", 0),
        ("s3_connect_timeout_seconds", 31),
        ("s3_read_timeout_seconds", 0),
        ("s3_read_timeout_seconds", 61),
        ("s3_max_attempts", 0),
        ("s3_max_attempts", 6),
    ],
)
def test_s3_reliability_settings_reject_unsafe_values(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_s3_client_uses_bounded_timeouts_and_retries(monkeypatch):
    captured = {}
    fake_client = object()

    def create_client(service_name, **kwargs):
        captured["service_name"] = service_name
        captured.update(kwargs)
        return fake_client

    monkeypatch.setattr("app.storage.s3.boto3.client", create_client)

    service = S3StorageService()

    assert service.client is fake_client
    assert captured["service_name"] == "s3"

    config = captured["config"]

    assert config.connect_timeout == settings.s3_connect_timeout_seconds
    assert config.read_timeout == settings.s3_read_timeout_seconds
    assert config.retries["mode"] == "standard"
    assert config.retries["total_max_attempts"] == settings.s3_max_attempts
