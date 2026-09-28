import asyncio
import hashlib
import secrets
import uuid
import weakref
from datetime import datetime, timedelta, timezone

import anyio
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.config import settings

password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return password_hasher.verify(encoded, password)
    except (VerifyMismatchError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# Async password hashing for request handlers
# --------------------------------------------------------------------------- #
# Argon2 is deliberately CPU- and memory-heavy. Running it directly inside an async handler
# blocks the event loop, so request handlers use the async wrappers below: the work runs in a
# worker thread, and a capacity limiter allows at most ``password_hash_concurrency`` Argon2
# operations at once (the rest wait). The Argon2 parameters above are unchanged, and the
# synchronous functions remain for scripts and tests.
_limiters: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, anyio.CapacityLimiter]" = (
    weakref.WeakKeyDictionary()
)


def password_limiter() -> anyio.CapacityLimiter:
    """The Argon2 limiter for the running event loop (created on first use)."""
    loop = asyncio.get_running_loop()
    limiter = _limiters.get(loop)
    if limiter is None:
        limiter = anyio.CapacityLimiter(settings.password_hash_concurrency)
        _limiters[loop] = limiter
    return limiter


async def hash_password_async(password: str) -> str:
    return await anyio.to_thread.run_sync(hash_password, password, limiter=password_limiter())


async def verify_password_async(password: str, encoded: str) -> bool:
    return await anyio.to_thread.run_sync(
        verify_password, password, encoded, limiter=password_limiter()
    )


def create_access_token(user_id: uuid.UUID) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_minutes),
        },
        settings.secret_key,
        algorithm="HS256",
    )


def decode_access_token(token: str) -> uuid.UUID:
    payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Invalid token type")
    return uuid.UUID(payload["sub"])


def new_refresh_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(48)
    return raw, token_digest(raw)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)