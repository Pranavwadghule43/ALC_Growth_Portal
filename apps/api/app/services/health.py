"""Dependency checks behind ``GET /api/health/ready`` (Phase 5B-3).

Each check does the smallest read-only operation that proves the dependency is usable:

* database: ``SELECT 1`` on the application's SQLAlchemy engine (no table is touched)
* redis:    ``PING`` on the application's existing Redis client (the login-limiter client)
* storage:  S3 ``HeadBucket`` on the configured bucket, or for local development storage
            that the evidence directory exists and is readable and writable

Every check is tried once with a short timeout, never retried and never cached: readiness
reports the current state, it is not a recovery mechanism. Nothing is written, uploaded,
listed or deleted.

The public result is only ``"ok"`` or ``"error"`` per check. Why a check failed (exception
type only, never its message, which can contain hosts, bucket names or SQL) goes to the
server log.

Redis is a required production dependency (``REDIS_URL`` is mandatory in production), so
readiness reports it honestly. The login limiter keeps working on its bounded in-process
fallback while Redis is down: the application is degraded, and readiness says "not ready".
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable

import structlog
from sqlalchemy import text

from app.config import settings
from app.database import engine
from app.services.login_limiter import redis_client
from app.storage import storage_service
from app.storage.local import LocalStorageService
from app.storage.s3 import S3StorageService

CHECK_TIMEOUT_SECONDS = 3.0
OK = "ok"
ERROR = "error"

log = structlog.get_logger("app.health")


async def check_database() -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def check_redis() -> None:
    await redis_client().ping()


# A blocked S3 call cannot be cancelled once its thread has started. While one is still
# running, further probes fail immediately instead of stacking up more threads.
_storage_check_running = False


def _head_bucket() -> None:
    global _storage_check_running
    try:
        storage_service.client.head_bucket(Bucket=settings.s3_bucket)
    finally:
        _storage_check_running = False


def _local_storage_usable() -> None:
    root = storage_service.root
    if not root.is_dir() or not os.access(root, os.R_OK | os.W_OK):
        raise OSError("local evidence directory is not usable")


async def check_storage() -> None:
    global _storage_check_running
    if isinstance(storage_service, S3StorageService):
        if _storage_check_running:
            raise TimeoutError("previous storage check has not returned")
        _storage_check_running = True
        await asyncio.to_thread(_head_bucket)
    elif isinstance(storage_service, LocalStorageService):
        await asyncio.to_thread(_local_storage_usable)
    else:
        raise RuntimeError("unknown storage backend")


# The dependencies the application genuinely needs to serve normal traffic.
CHECKS: dict[str, Callable[[], Awaitable[None]]] = {
    "database": check_database,
    "redis": check_redis,
    "storage": check_storage,
}


async def _run_check(name: str, check: Callable[[], Awaitable[None]], timeout: float) -> str:
    try:
        await asyncio.wait_for(check(), timeout=timeout)
    except Exception as exc:  # any failure, including a timeout, means "not usable"
        # Type only: the message can hold hosts, bucket names or SQL. No stack trace: an
        # outage is an expected condition and probes repeat every few seconds.
        log.warning("readiness_check_failed", check=name, error_type=type(exc).__name__)
        return ERROR
    return OK


async def readiness(timeout: float | None = None) -> tuple[bool, dict[str, str]]:
    """(ready, {check name: "ok" | "error"}). Checks run concurrently, once each."""
    timeout = CHECK_TIMEOUT_SECONDS if timeout is None else timeout
    names = list(CHECKS)
    results = await asyncio.gather(*(_run_check(name, CHECKS[name], timeout) for name in names))
    checks = dict(zip(names, results, strict=True))
    return all(state == OK for state in checks.values()), checks
