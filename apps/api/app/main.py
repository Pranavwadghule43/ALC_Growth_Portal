import math
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.observability import (
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    configure_logging,
    mark_logged,
    was_logged,
)
from app.routes import admin, auth, health, leaderboard, portal

# JSON lines in production, readable lines in development/test; level from LOG_LEVEL.
configure_logging(settings.app_env, settings.log_level)
log = structlog.get_logger("app.lifecycle")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Names of what is configured only: never URLs, credentials or the environment itself.
    log.info(
        "application_started", app_env=settings.app_env, log_level=settings.log_level,
        storage_backend=settings.storage_backend,
        trusted_proxies_configured=bool(settings.trusted_proxy_networks),
    )
    yield
    log.info("application_stopped")


# Production configuration is validated when ``app.config.settings`` is created (see
# ``app.config.validate_settings``): an unsafe production setup never reaches this point.
# In production the interactive docs and the OpenAPI schema are not served at all.
_production = settings.app_env == "production"
app = FastAPI(
    title="ALC Growth Portal API",
    version="1.0.0",
    docs_url=None if _production else "/docs",
    redoc_url=None if _production else "/redoc",
    openapi_url=None if _production else "/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
    expose_headers=[REQUEST_ID_HEADER],
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(self), geolocation=()"
        if settings.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


app.add_middleware(SecurityHeadersMiddleware)
# Added last = outermost: request id, request log and the safety net for unexpected errors.
app.add_middleware(RequestContextMiddleware)


def _json_safe(value: Any) -> Any:
    """Make one piece of Pydantic error data strictly JSON-serialisable.

    A validator that raises ``ValueError`` / ``AssertionError`` leaves the exception object
    itself in ``ctx["error"]``; it is replaced by its message (never its repr). Other
    values (dates, UUIDs, Decimals, bytes, NaN echoed from the input, ...) are encoded, and
    anything unencodable becomes its ``str()``, so the handler itself can never fail.
    """
    if isinstance(value, BaseException):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    try:
        return _json_safe(jsonable_encoder(value))
    except Exception:
        return str(value)


def validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    return [_json_safe(error) for error in exc.errors()]


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Please check the submitted information",
                "details": validation_details(exc),
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    # Normally RequestContextMiddleware has already logged the error and answered; this
    # covers anything that fails outside it. The exception type and stack trace go to the
    # server log, never to the client.
    if not was_logged(exc):
        mark_logged(exc)
        structlog.get_logger("app.request").error(
            "unhandled_error", error_type=type(exc).__name__, path=request.url.path, exc_info=exc
        )
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred"}},
    )


app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(portal.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(leaderboard.router, prefix="/api")
