import logging

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.routes import admin, auth, portal

structlog.configure(
    processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()]
)
logging.basicConfig(level=logging.INFO)

if settings.app_env == "production" and (
    settings.secret_key.startswith("development-only")
    or not settings.cookie_secure
    or settings.storage_backend == "local"
):
    raise RuntimeError("Production requires a strong SECRET_KEY and COOKIE_SECURE=true")
app = FastAPI(
    title="ALC Growth Portal API",
    version="1.0.0",
    docs_url="/docs" if settings.app_env != "production" else None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
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


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Please check the submitted information",
                "details": exc.errors(),
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    structlog.get_logger().exception("unhandled_error", path=request.url.path, error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred"}},
    )


@app.get("/api/health")
async def health():
    return {"status": "ok"}


app.include_router(auth.router, prefix="/api")
app.include_router(portal.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
