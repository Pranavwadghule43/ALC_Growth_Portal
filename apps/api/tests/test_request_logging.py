"""Phase 5B-3: request ids, request logging, log format and log safety.

* Every response carries a server-generated ``X-Request-ID``; the same id is on every log
  line written for that request.
* One ``request_completed`` line per request with method, path, status and duration.
* Nothing sensitive is logged: no password, cookie, token, Authorization header, query
  string, login identifier, or configuration secret.
* Unexpected errors: generic 500 for the client, stack trace only in the server log.
"""
import io
import json
import logging
import re

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.config import Settings, settings
from app.database import engine
from app.main import app
from app.observability import (
    REDACTED,
    RequestContextMiddleware,
    build_formatter,
    configure_logging,
    is_sensitive_field,
    new_request_id,
    redact_sensitive,
    route_template,
)
from tests.conftest import login

ADMIN = ("admin", "StrongAdminPass!", "ADMIN")
ALC_A = ("00010001", "StrongAlcPassA!", "ALC")
REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
CONFIG_SECRETS = (settings.secret_key, settings.database_url, settings.redis_url,
                  settings.s3_secret_key)


def completed(logs, path=None) -> list[dict]:
    entries = logs.named("request_completed")
    return [e for e in entries if path is None or e["path"] == path]


# --------------------------------------------------------------------------- #
# Request id
# --------------------------------------------------------------------------- #
async def test_every_response_has_a_generated_request_id(client):
    seen = set()
    for path in ("/api/health/live", "/api/auth/me", "/api/does-not-exist", "/api/admin/alcs"):
        request_id = (await client.get(path)).headers["x-request-id"]
        assert REQUEST_ID.match(request_id), path
        seen.add(request_id)
    assert len(seen) == 4  # a different id for every request
    assert len({new_request_id() for _ in range(1000)}) == 1000


async def test_the_same_request_id_is_on_the_request_log(client, logs):
    first = await client.get("/api/auth/me")
    second = await client.get("/api/auth/me")
    entries = completed(logs, "/api/auth/me")
    assert [entry["request_id"] for entry in entries] == [
        first.headers["x-request-id"], second.headers["x-request-id"]]
    assert entries[0]["request_id"] != entries[1]["request_id"]


async def test_a_client_supplied_request_id_is_never_trusted(client, logs):
    forged = "forged-id\"} {\"event\":\"fake"
    response = await client.get("/api/auth/me", headers={"X-Request-ID": forged})
    assert REQUEST_ID.match(response.headers["x-request-id"])
    assert "forged-id" not in response.headers["x-request-id"]
    assert "forged-id" not in logs.text


async def test_request_id_is_readable_by_a_cross_origin_frontend(client):
    response = await client.get("/api/health/live", headers={"Origin": "http://localhost:5173"})
    assert "x-request-id" in response.headers["access-control-expose-headers"].lower()


# --------------------------------------------------------------------------- #
# Request completion log
# --------------------------------------------------------------------------- #
async def test_request_completion_is_logged_with_method_path_status_and_duration(client, logs):
    assert (await client.get("/api/auth/me")).status_code == 401
    [entry] = completed(logs, "/api/auth/me")
    assert (entry["event"], entry["level"], entry["logger"]) == (
        "request_completed", "info", "app.request")
    assert (entry["method"], entry["path"], entry["status_code"]) == ("GET", "/api/auth/me", 401)
    assert isinstance(entry["duration_ms"], (int, float)) and entry["duration_ms"] >= 0
    assert entry["client_ip"] == "127.0.0.1"
    assert "timestamp" in entry and "user_id" not in entry  # anonymous request


async def test_status_codes_are_logged_for_404_and_validation_errors(client, logs):
    assert (await client.get("/api/does-not-exist")).status_code == 404
    bad = await client.post("/api/auth/login", json={"identifier": "x"})
    assert bad.status_code == 422
    assert completed(logs, "/api/does-not-exist")[0]["status_code"] == 404
    [entry] = completed(logs, "/api/auth/login")
    assert (entry["method"], entry["status_code"], entry["level"]) == ("POST", 422, "info")


async def test_query_strings_are_never_logged(client, logs):
    await client.get("/api/auth/me?reset_token=QUERYSECRET123&search=ravi%40example.org")
    [entry] = completed(logs, "/api/auth/me")
    assert entry["path"] == "/api/auth/me"
    for leaked in ("QUERYSECRET123", "reset_token", "ravi", "example.org", "?"):
        assert leaked not in json.dumps(entry)


async def test_authenticated_requests_log_user_id_and_role_only(client, seeded, logs):
    assert (await login(client, *ADMIN)).status_code == 200
    assert (await client.get("/api/admin/alcs")).status_code == 200
    [entry] = completed(logs, "/api/admin/alcs")
    assert (entry["user_id"], entry["role"]) == (str(seeded["admin"].id), "ADMIN")
    assert "admin" not in {k: v for k, v in entry.items() if k not in ("role", "path")}.values()


async def test_paths_with_ids_also_log_the_route_template(client, seeded, logs):
    assert (await login(client, *ADMIN)).status_code == 200
    alc_id = seeded["alc_a"].id
    await client.get(f"/api/admin/alcs/{alc_id}")
    [entry] = completed(logs, f"/api/admin/alcs/{alc_id}")
    assert entry["route"] == "/api/admin/alcs/{alc_id}"
    assert route_template("/api/x/abc/files/7", {"item": "abc", "n": 7}) == (
        "/api/x/{item}/files/{n}")
    assert route_template("/api/x", {}) == "/api/x"


# --------------------------------------------------------------------------- #
# Nothing sensitive in the log
# --------------------------------------------------------------------------- #
async def test_login_never_logs_password_cookie_or_token(client, seeded, logs):
    response = await client.post(
        "/api/auth/login",
        json={"identifier": ALC_A[0], "password": ALC_A[1]},
        headers={"Authorization": "Bearer HEADERSECRET-abc.def.ghi",
                 "Cookie": "tracking=COOKIESECRET42"},
    )
    assert response.status_code == 200
    access = client.cookies.get("access_token")
    refresh = client.cookies.get("refresh_token")
    csrf = client.cookies.get("csrf_token")
    assert access and refresh and csrf
    client.headers["X-CSRF-Token"] = csrf
    assert (await client.get("/api/portal/dashboard")).status_code == 200
    for secret in (ALC_A[1], access, refresh, csrf, "HEADERSECRET", "COOKIESECRET42", "Bearer"):
        assert secret not in logs.text
    for word in ("authorization", "cookie", "set-cookie", "password\":"):
        assert word not in logs.text.lower()
    [event] = logs.named("login_succeeded")
    assert (event["user_id"], event["role"], event["kind"]) == (
        str(seeded["a"].id), "ALC", "login")
    assert event["change_required"] is False
    assert ALC_A[0] not in json.dumps(event)  # the typed identifier is not logged either


async def test_failed_login_logs_neither_the_identifier_nor_the_password(client, seeded, logs):
    for identifier, portal in ((ALC_A[0], "ALC"), ("no-such-user-zz", "ALC"), ("admin", "ADMIN")):
        assert (await login(client, identifier, "WrongPassword-9x!", portal)).status_code == 401
    events = logs.named("login_failed")
    assert [(e["scope"], e["level"]) for e in events] == [
        ("portal", "info"), ("portal", "info"), ("admin", "info")]
    # Existing and unknown identifiers produce the same log line: nothing reveals which is real.
    assert set(events[0]) == set(events[1])
    for leaked in ("WrongPassword-9x!", "no-such-user-zz", ALC_A[0]):
        assert leaked not in logs.text
    assert all("user_id" not in e for e in events)


async def test_rate_limited_login_is_logged_without_the_identifier(client, seeded, logs,
                                                                  monkeypatch):
    from app.services import login_limiter

    monkeypatch.setattr(login_limiter.settings, "login_pair_failure_limit", 1)
    assert (await login(client, "throttled-user-qq", "WrongPassword-9x!", "ALC")).status_code == 401
    assert (await login(client, "throttled-user-qq", "WrongPassword-9x!", "ALC")).status_code == 429
    [event] = logs.named("login_rate_limited")
    assert (event["scope"], event["limit"], event["level"]) == (
        "portal", "client_ip_and_identifier", "warning")
    assert "throttled-user-qq" not in logs.text


async def test_configuration_secrets_never_reach_the_log(client, seeded, logs):
    async with app.router.lifespan_context(app):
        await login(client, *ADMIN)
        await client.get("/api/admin/alcs")
        await client.get("/api/health/ready")
    assert logs.named("application_started") and logs.named("application_stopped")
    for secret in CONFIG_SECRETS:
        assert secret not in logs.text
    for fragment in ("postgresql", "sqlite", "redis://", "minioadmin"):
        assert fragment not in logs.text


def test_sensitively_named_fields_are_redacted():
    secrets = {
        "password": "pw", "new_password": "pw", "Authorization": "Bearer x", "cookie": "a=b",
        "set-cookie": "a=b", "access_token": "t", "refresh_token": "t", "csrf_token": "t",
        "jwt": "t", "SECRET_KEY": "k", "s3_secret_key": "k", "s3_access_key": "k",
        "database_url": "postgresql://u:p@h/d", "redis_url": "redis://:p@h/0", "body": "{}",
    }
    safe = {"event": "x", "method": "GET", "path": "/api/x", "status_code": 200,
            "duration_ms": 1.5, "user_id": "u-1", "role": "ADMIN", "request_id": "r"}
    result = redact_sensitive(None, "info", {**secrets, **safe})
    assert all(result[key] == REDACTED for key in secrets)
    assert {key: result[key] for key in safe} == safe
    assert not any(is_sensitive_field(key) for key in safe)


# --------------------------------------------------------------------------- #
# Unexpected errors
# --------------------------------------------------------------------------- #
def failing_app() -> FastAPI:
    isolated = FastAPI()
    isolated.add_middleware(RequestContextMiddleware)

    @isolated.get("/boom")
    async def boom():
        raise RuntimeError("database password is hunter2-INTERNAL-DETAIL")

    return isolated


async def test_unhandled_exception_is_a_generic_500_with_request_id(logs):
    # raise_app_exceptions=False: see the response a real server sends to the browser.
    transport = ASGITransport(app=failing_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        response = await c.get("/boom?token=QUERYSECRET")
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred"}}
    request_id = response.headers["x-request-id"]
    assert REQUEST_ID.match(request_id)
    for leaked in ("hunter2", "INTERNAL-DETAIL", "RuntimeError", "Traceback", "QUERYSECRET"):
        assert leaked not in response.text
    [error] = logs.named("unhandled_error")
    assert (error["level"], error["error_type"], error["request_id"]) == (
        "error", "RuntimeError", request_id)
    assert (error["method"], error["path"]) == ("GET", "/boom")
    assert "Traceback" in error["exception"] and "boom" in error["exception"]  # server-side only
    [entry] = completed(logs, "/boom")
    assert (entry["status_code"], entry["level"], entry["request_id"]) == (500, "error", request_id)
    assert "QUERYSECRET" not in logs.text


async def test_real_app_error_is_logged_once_and_still_reaches_the_server(logs):
    """On the real application: generic 500 + request id, exactly one stack trace, and the
    exception still propagates to the ASGI server (Starlette's normal behaviour)."""
    from app.observability import was_logged

    async def boom():
        raise RuntimeError("hunter2-INTERNAL-DETAIL")

    app.router.add_api_route("/api/_test_boom", boom, methods=["GET"])
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            response = await c.get("/api/_test_boom")
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"
        assert "hunter2" not in response.text
        [error] = logs.named("unhandled_error")  # once, not once per layer
        assert error["request_id"] == response.headers["x-request-id"]
        assert logs.text.count("Traceback") == 1
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            with pytest.raises(RuntimeError) as raised:
                await c.get("/api/_test_boom")
        assert was_logged(raised.value)
        # Uvicorn's own "Exception in ASGI application" record for it is dropped: the
        # trace is already in the log with its request id.
        record = logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 1,
                                   "Exception in ASGI application\n", None,
                                   (RuntimeError, raised.value, None))
        ours = [h for h in logging.getLogger().handlers if getattr(h, "alc_app_handler", False)]
        assert ours and not ours[0].filter(record)
        other = logging.LogRecord("uvicorn.error", logging.ERROR, __file__, 1, "x", None,
                                  (ValueError, ValueError("not logged yet"), None))
        assert ours[0].filter(other)
    finally:
        app.router.routes[:] = [r for r in app.router.routes
                                if getattr(r, "path", "") != "/api/_test_boom"]


async def test_database_errors_do_not_log_bound_values():
    assert engine.sync_engine.hide_parameters is True


# --------------------------------------------------------------------------- #
# Format and level
# --------------------------------------------------------------------------- #
def render(json_logs: bool) -> str:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(build_formatter(json_logs))
    logger = logging.getLogger("tests.format")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        import structlog

        structlog.get_logger("tests.format").info("request_completed", method="GET",
                                                  status_code=200, duration_ms=1.2)
        logger.warning("plain stdlib message from %s", "a library")
    finally:
        logger.removeHandler(handler)
        logger.propagate = True
    return stream.getvalue()


def test_production_logs_are_one_json_object_per_line():
    first, second = [json.loads(line) for line in render(json_logs=True).splitlines()]
    assert {"timestamp", "level", "logger", "event", "method", "status_code",
            "duration_ms"} <= set(first)
    assert (first["level"], first["logger"], first["event"]) == (
        "info", "tests.format", "request_completed")
    assert first["timestamp"].endswith("Z")
    # Standard-library loggers (Uvicorn, SQLAlchemy, ...) are rendered the same way.
    assert (second["level"], second["event"]) == ("warning", "plain stdlib message from a library")


def test_development_logs_are_human_readable():
    lines = render(json_logs=False).splitlines()
    assert len(lines) == 2
    with pytest.raises(json.JSONDecodeError):
        json.loads(lines[0])
    assert "request_completed" in lines[0] and "status_code=200" in lines[0]
    assert "\x1b[" not in lines[0]  # no colour codes


def test_uvicorn_access_log_is_replaced_and_error_log_is_unified():
    configure_logging(settings.app_env, settings.log_level)
    access = logging.getLogger("uvicorn.access")
    assert (access.handlers, access.propagate) == ([], False)  # it would log query strings
    assert logging.getLogger("uvicorn.error").propagate is True
    ours = [h for h in logging.getLogger().handlers if getattr(h, "alc_app_handler", False)]
    assert len(ours) == 1  # configuring twice does not duplicate the handler
    configure_logging(settings.app_env, settings.log_level)
    ours = [h for h in logging.getLogger().handlers if getattr(h, "alc_app_handler", False)]
    assert len(ours) == 1


def test_log_level_setting_is_validated():
    assert Settings().log_level == "INFO"  # never DEBUG by default
    assert Settings(app_env="production").log_level == "INFO"
    assert Settings(log_level=" debug ").log_level == "DEBUG"
    for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        assert Settings(log_level=level).log_level == level
    for bad in ("VERBOSE", "TRACE", "", "10"):
        with pytest.raises(ValidationError, match="LOG_LEVEL must be one of"):
            Settings(log_level=bad)


def test_noisy_libraries_stay_quiet_even_at_debug():
    # botocore / httpx / database drivers print signed headers, full URLs and SQL values at
    # DEBUG. LOG_LEVEL=DEBUG must not switch that on.
    try:
        configure_logging("production", "DEBUG")
        assert logging.getLogger().level == logging.DEBUG
        for name in ("botocore", "boto3", "urllib3", "aiosqlite", "asyncpg", "httpx", "multipart"):
            assert logging.getLogger(name).getEffectiveLevel() == logging.WARNING, name
            assert not logging.getLogger(f"{name}.child").isEnabledFor(logging.INFO)
    finally:
        configure_logging(settings.app_env, settings.log_level)
