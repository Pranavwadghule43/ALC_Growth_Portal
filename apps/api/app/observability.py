"""Production logging and request correlation (Phase 5B-3).

Logging
    One handler on the root logger renders every record, whether it comes from structlog
    (the application) or from the standard library (Uvicorn, SQLAlchemy, ...):

    * ``APP_ENV=production``  -> one JSON object per line (machine-readable)
    * ``development`` / ``test`` -> a readable ``key=value`` line

    Level comes from ``LOG_LEVEL`` (default INFO). Output goes to stderr. Third-party
    libraries that can print URLs, signed headers or SQL values stay at WARNING even when
    ``LOG_LEVEL=DEBUG``.

Request correlation
    ``RequestContextMiddleware`` gives every HTTP request a server-generated request id. It
    is returned in the ``X-Request-ID`` response header and attached to every log line
    written while the request is handled. An ``X-Request-ID`` sent by the client is ignored:
    the value is never trusted, so it cannot be used to forge or pollute log lines.

Request log
    One ``request_completed`` line per request: method, path (never the query string),
    route template, status code, duration, client IP (from the trusted-proxy rules in
    ``login_limiter.client_ip``) and, when the request was authenticated, the user id and
    role. Never logged: request or response bodies, headers, cookies, tokens, passwords.
    Successful health probes are logged at DEBUG so they do not flood the log.

Safety net
    ``redact_sensitive`` replaces the value of any log field whose NAME looks sensitive
    (password, token, secret, cookie, authorization, connection URLs). The rule is still
    "do not log sensitive input in the first place"; this only catches a future mistake.
"""
from __future__ import annotations

import logging
import secrets
import sys
import time

import structlog
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.services.login_limiter import client_ip

REQUEST_ID_HEADER = "X-Request-ID"
HEALTH_PATHS = frozenset({"/api/health", "/api/health/live", "/api/health/ready"})
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
REDACTED = "[REDACTED]"
# Libraries whose DEBUG/INFO output can contain request URLs, signed headers, SQL values or
# form fields. They are held at WARNING whatever LOG_LEVEL says, so turning on DEBUG to
# investigate a problem never starts writing those details to the log.
QUIET_LIBRARY_LOGGERS = (
    "botocore", "boto3", "s3transfer", "urllib3", "aiosqlite", "asyncpg", "httpx", "httpcore",
    "multipart", "python_multipart", "passlib",
)
INTERNAL_ERROR_BODY = (
    b'{"error":{"code":"INTERNAL_ERROR","message":"An unexpected error occurred"}}'
)

# A field is redacted when its name is one of these, or contains one of the fragments.
_SENSITIVE_NAMES = frozenset({
    "authorization", "cookie", "set-cookie", "jwt", "database_url", "redis_url", "dsn",
    "s3_access_key", "access_key", "credentials", "body", "request_body",
})
_SENSITIVE_FRAGMENTS = ("password", "passwd", "secret", "token", "csrf", "api_key", "apikey")


def is_sensitive_field(name: str) -> bool:
    lowered = str(name).lower()
    return lowered in _SENSITIVE_NAMES or any(part in lowered for part in _SENSITIVE_FRAGMENTS)


def redact_sensitive(_logger, _method, event_dict: dict) -> dict:
    """structlog processor: never let a sensitively named field reach the output."""
    for key in event_dict:
        if is_sensitive_field(key):
            event_dict[key] = REDACTED
    return event_dict


def _shared_processors() -> list:
    return [
        structlog.contextvars.merge_contextvars,  # request_id (and anything bound per request)
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        redact_sensitive,
    ]


def build_formatter(json_logs: bool) -> logging.Formatter:
    """The formatter used for every record: JSON lines, or a readable development line."""
    if json_logs:
        final = [structlog.processors.format_exc_info, structlog.processors.JSONRenderer()]
    else:
        final = [structlog.dev.ConsoleRenderer(colors=False)]
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_shared_processors(),
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *final],
    )


def configure_logging(app_env: str, log_level: str = "INFO") -> None:
    """Install the single log handler. Safe to call more than once."""
    structlog.configure(
        processors=[*_shared_processors(), structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(build_formatter(json_logs=app_env == "production"))
    handler.addFilter(_AlreadyLoggedFilter())
    handler.alc_app_handler = True
    root = logging.getLogger()
    root.handlers[:] = [h for h in root.handlers if not getattr(h, "alc_app_handler", False)]
    root.addHandler(handler)
    root.setLevel(log_level)
    # Uvicorn installs its own handlers: route its messages through ours instead, and turn
    # its access log off. That log prints the full URL including the query string; the
    # ``request_completed`` line below replaces it and logs the path only.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    for name in QUIET_LIBRARY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def mark_logged(exc: BaseException) -> None:
    exc._alc_logged = True


def was_logged(exc: BaseException | None) -> bool:
    return bool(getattr(exc, "_alc_logged", False))


class _AlreadyLoggedFilter(logging.Filter):
    """Drop Uvicorn's "Exception in ASGI application" record for an error the request
    middleware has already logged (with its request id), so each stack trace appears once."""

    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if isinstance(record.exc_info, tuple) else None
        return not was_logged(exc)


def new_request_id() -> str:
    """A random, server-generated id (128 bits, hex). Contains no user data."""
    return secrets.token_hex(16)


def route_template(path: str, path_params: dict | None) -> str:
    """``/api/x/3f2a...`` -> ``/api/x/{item_id}``: the path with matched ids replaced by
    their parameter names, so log lines for one endpoint group together."""
    if not path_params:
        return path
    names = {str(value): name for name, value in path_params.items()}
    return "/".join(
        f"{{{names[segment]}}}" if segment in names else segment for segment in path.split("/")
    )


def set_actor(request: Request, user_id, role) -> None:
    """Remember who made this request so the request log can name them (id and role only)."""
    request.state.actor_id = str(user_id)
    request.state.actor_role = getattr(role, "value", role)


class RequestContextMiddleware:
    """Request id + one completion log line per request + safe handling of unexpected errors.

    Added last, so it is the outermost application middleware: it sees the final status of
    every response and catches any exception the application did not handle.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.log = structlog.get_logger("app.request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = new_request_id()
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        status_code: int | None = None

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            # The stack trace stays in the server log; the client gets a generic 500.
            self.log.error(
                "unhandled_error", error_type=type(exc).__name__, method=scope["method"],
                path=scope["path"], exc_info=exc,
            )
            mark_logged(exc)
            if status_code is None:  # otherwise the response has started and cannot be replaced
                await send_with_request_id({
                    "type": "http.response.start", "status": 500,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(INTERNAL_ERROR_BODY)).encode())],
                })
                await send({"type": "http.response.body", "body": INTERNAL_ERROR_BODY})
            # Re-raise, as Starlette's own error middleware does: the server (and a test
            # client) still sees the failure. It is not logged a second time, see below.
            raise
        finally:
            self._log_completion(scope, state, status_code, started)
            structlog.contextvars.clear_contextvars()

    def _log_completion(self, scope: Scope, state: dict, status_code: int | None,
                        started: float) -> None:
        path = scope["path"]
        status = status_code if status_code is not None else 500
        fields = {
            "method": scope["method"],
            "path": path,  # never the query string
            "status_code": status,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "client_ip": client_ip(Request(scope)),
        }
        route = route_template(path, scope.get("path_params"))
        if route != path:
            fields["route"] = route
        if state.get("actor_id"):
            fields["user_id"] = state["actor_id"]
            fields["role"] = state.get("actor_role")
        if status >= 500:
            level = logging.WARNING if path in HEALTH_PATHS else logging.ERROR
        elif path in HEALTH_PATHS:
            level = logging.DEBUG  # successful probes must not flood the log
        else:
            level = logging.INFO
        self.log.log(level, "request_completed", **fields)
