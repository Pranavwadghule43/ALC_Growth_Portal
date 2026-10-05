"""Phase 5B-3: liveness and readiness endpoints.

* ``GET /api/health/live`` (and the original ``GET /api/health``): the process is alive. It
  never touches a dependency.
* ``GET /api/health/ready``: database, Redis and storage are usable -> 200, otherwise 503.
  The response holds only ``ok`` / ``error`` words; the reason goes to the server log.

No real PostgreSQL, Redis or S3 is needed: the dependency checks are replaced by fakes, and
the check functions themselves are exercised against stand-in clients.
"""
import asyncio
import time

import pytest

from app.config import settings
from app.services import health
from app.storage.local import LocalStorageService
from app.storage.s3 import S3StorageService

LEAK = "postgresql+asyncpg://alc:Sup3rS3cretPw@db-internal.example:5432/alc_prod"
ALL_OK = {"database": "ok", "redis": "ok", "storage": "ok"}


class Recorder:
    """A fake dependency check: counts its calls and fails when told to."""

    def __init__(self, error: Exception | None = None, hang: bool = False) -> None:
        self.calls = 0
        self.error = error
        self.hang = hang

    async def __call__(self) -> None:
        self.calls += 1
        if self.hang:
            await asyncio.sleep(30)
        if self.error:
            raise self.error


@pytest.fixture
def checks(monkeypatch):
    """Replace all three dependency checks with healthy fakes; a test breaks the ones it wants."""
    fakes = {name: Recorder() for name in ("database", "redis", "storage")}
    monkeypatch.setattr(health, "CHECKS", fakes)
    return fakes


# --------------------------------------------------------------------------- #
# Liveness
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ["/api/health/live", "/api/health"])
async def test_liveness_returns_200_with_a_minimal_body(client, path):
    response = await client.get(path)  # no login: the endpoint is public
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


async def test_liveness_does_not_depend_on_any_dependency(client, checks):
    for fake in checks.values():
        fake.error = ConnectionError("dependency is down")
    response = await client.get("/api/health/live")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})
    assert [fake.calls for fake in checks.values()] == [0, 0, 0]  # nothing was even tried
    assert (await client.get("/api/health/ready")).status_code == 503  # while not ready


async def test_liveness_exposes_no_internal_configuration(client):
    response = await client.get("/api/health/live")
    exposed = (response.text + " " + " ".join(f"{k}: {v}" for k, v in response.headers.items()))
    for secret in (settings.secret_key, settings.database_url, settings.redis_url,
                   settings.s3_secret_key, settings.s3_access_key, settings.s3_bucket,
                   str(settings.s3_endpoint_url), settings.app_env, settings.local_storage_path):
        assert secret not in exposed
    assert set(response.json()) == {"status"}


# --------------------------------------------------------------------------- #
# Readiness
# --------------------------------------------------------------------------- #
async def test_ready_when_every_dependency_is_healthy(client, checks):
    response = await client.get("/api/health/ready")  # public as well
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": ALL_OK}
    assert response.headers["cache-control"] == "no-store"
    assert [fake.calls for fake in checks.values()] == [1, 1, 1]  # each tried exactly once


@pytest.mark.parametrize("failing", ["database", "redis", "storage"])
async def test_not_ready_when_one_dependency_is_unavailable(client, checks, failing):
    checks[failing].error = ConnectionError("down")
    response = await client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "checks": {**ALL_OK, failing: "error"}}
    assert [fake.calls for fake in checks.values()] == [1, 1, 1]  # no retry, others still run


async def test_not_ready_with_several_failures(client, checks):
    checks["database"].error = OSError("down")
    checks["storage"].error = TimeoutError("slow")
    response = await client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready", "checks": {"database": "error", "redis": "ok", "storage": "error"}}
    for fake in checks.values():
        fake.error = RuntimeError("everything is down")
    everything = await client.get("/api/health/ready")
    assert everything.status_code == 503
    assert set(everything.json()["checks"].values()) == {"error"}


async def test_readiness_response_holds_only_safe_words(client, checks, logs):
    checks["database"].error = RuntimeError(f"could not connect to {LEAK}")
    checks["redis"].error = ConnectionError("redis://:R3disPassw0rd@10.20.30.40:6379/0 refused")
    checks["storage"].error = PermissionError("AccessDenied for bucket alc-private-evidence")
    response = await client.get("/api/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert set(body) == {"status", "checks"}
    assert set(body["checks"]) == {"database", "redis", "storage"}
    assert set(body["checks"].values()) <= {"ok", "error"}
    # Neither the response nor the log repeats the exception text (hosts, passwords, bucket).
    for leaked in (LEAK, "Sup3rS3cretPw", "db-internal.example", "R3disPassw0rd", "10.20.30.40",
                   "alc-private-evidence", "AccessDenied", "Traceback"):
        assert leaked not in response.text
        assert leaked not in logs.text


async def test_readiness_is_not_cached(client, checks):
    assert (await client.get("/api/health/ready")).status_code == 200
    checks["redis"].error = ConnectionError("down")
    assert (await client.get("/api/health/ready")).status_code == 503
    checks["redis"].error = None
    assert (await client.get("/api/health/ready")).status_code == 200
    assert checks["redis"].calls == 3  # asked every time


async def test_a_hanging_dependency_fails_fast(client, checks, monkeypatch):
    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.05)
    checks["database"].hang = True
    started = time.perf_counter()
    response = await client.get("/api/health/ready")
    assert time.perf_counter() - started < 5  # not the 30 seconds the check would take
    assert response.status_code == 503
    assert response.json()["checks"] == {**ALL_OK, "database": "error"}


async def test_health_routes_are_read_only(client, checks):
    for path in ("/api/health", "/api/health/live", "/api/health/ready"):
        assert (await client.post(path)).status_code == 405
        assert (await client.delete(path)).status_code == 405


# --------------------------------------------------------------------------- #
# Health logging
# --------------------------------------------------------------------------- #
async def test_failed_readiness_is_logged_as_a_warning_without_a_stack_trace(
    client, checks, logs
):
    checks["redis"].error = ConnectionError("down")
    response = await client.get("/api/health/ready")
    [failure] = logs.named("readiness_check_failed")
    assert (failure["level"], failure["check"], failure["error_type"]) == (
        "warning", "redis", "ConnectionError")
    assert failure["request_id"] == response.headers["x-request-id"]
    assert "exception" not in failure and "Traceback" not in logs.text
    [completed] = logs.named("request_completed")
    assert (completed["level"], completed["status_code"]) == ("warning", 503)


async def test_successful_probes_are_logged_at_debug_only(client, checks, logs):
    for path in ("/api/health", "/api/health/live", "/api/health/ready"):
        assert (await client.get(path)).status_code == 200
    completed = logs.named("request_completed")
    assert [entry["path"] for entry in completed] == [
        "/api/health", "/api/health/live", "/api/health/ready"]
    assert {entry["level"] for entry in completed} == {"debug"}  # hidden at the default INFO
    assert logs.named("readiness_check_failed") == []


# --------------------------------------------------------------------------- #
# The real check functions, against stand-in clients
# --------------------------------------------------------------------------- #
async def test_database_check_runs_select_1_on_the_application_engine(monkeypatch):
    await health.check_database()  # the test engine (SQLite) answers SELECT 1
    statements = []

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def execute(self, statement):
            statements.append(str(statement))

    class Engine:
        def connect(self):
            return Connection()

    monkeypatch.setattr(health, "engine", Engine())
    await health.check_database()
    assert statements == ["SELECT 1"]  # no application table, nothing written


async def test_database_check_reports_a_connection_failure(monkeypatch):
    class Engine:
        def connect(self):
            raise ConnectionRefusedError(LEAK)

    monkeypatch.setattr(health, "engine", Engine())
    assert await health._run_check("database", health.check_database, 1.0) == "error"


async def test_redis_check_pings_the_existing_client(monkeypatch):
    class Client:
        pings = 0

        async def ping(self):
            Client.pings += 1
            return True

    monkeypatch.setattr(health, "redis_client", lambda: Client())
    await health.check_redis()
    assert Client.pings == 1

    class Down:
        async def ping(self):
            raise ConnectionError("refused")

    monkeypatch.setattr(health, "redis_client", lambda: Down())
    assert await health._run_check("redis", health.check_redis, 1.0) == "error"


def test_redis_check_reuses_the_login_limiter_client_configuration():
    from app.services import login_limiter

    assert health.redis_client is login_limiter.redis_client

    async def same_client_twice():
        return login_limiter.redis_client() is login_limiter.redis_client()

    assert asyncio.run(same_client_twice())


def s3_service(client) -> S3StorageService:
    service = S3StorageService.__new__(S3StorageService)  # no real boto3 client
    service.client = client
    return service


async def test_s3_storage_check_is_one_head_bucket_call(monkeypatch):
    class Client:
        calls = []

        def head_bucket(self, **kwargs):
            Client.calls.append(kwargs)

        def __getattr__(self, name):  # any other S3 operation would be a bug
            raise AssertionError(f"readiness must not call {name}")

    monkeypatch.setattr(health, "storage_service", s3_service(Client()))
    monkeypatch.setattr(health, "_storage_check_running", False)
    await health.check_storage()
    assert Client.calls == [{"Bucket": settings.s3_bucket}]
    assert health._storage_check_running is False


async def test_s3_storage_check_failure_and_no_pile_up(monkeypatch):
    class Client:
        calls = 0

        def head_bucket(self, **_):
            Client.calls += 1
            raise ConnectionError("endpoint unreachable")

    monkeypatch.setattr(health, "storage_service", s3_service(Client()))
    monkeypatch.setattr(health, "_storage_check_running", False)
    assert await health._run_check("storage", health.check_storage, 1.0) == "error"
    assert (Client.calls, health._storage_check_running) == (1, False)
    # While an earlier call is still blocked in its thread, a new probe fails at once
    # instead of starting another thread.
    monkeypatch.setattr(health, "_storage_check_running", True)
    assert await health._run_check("storage", health.check_storage, 1.0) == "error"
    assert Client.calls == 1


async def test_local_storage_check(monkeypatch, tmp_path):
    service = LocalStorageService.__new__(LocalStorageService)
    service.root = tmp_path
    monkeypatch.setattr(health, "storage_service", service)
    await health.check_storage()
    assert list(tmp_path.iterdir()) == []  # nothing was written
    service.root = tmp_path / "missing"
    assert await health._run_check("storage", health.check_storage, 1.0) == "error"


def test_readiness_covers_exactly_the_required_dependencies():
    assert list(health.CHECKS) == ["database", "redis", "storage"]
    assert 0 < health.CHECK_TIMEOUT_SECONDS <= 5
