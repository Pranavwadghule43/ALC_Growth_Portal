"""Small, fail-open Redis cache for expensive, fully materialized read responses.

Values are JSON only.  Version counters make invalidation immediate without key scans; old
values remain unreachable and expire naturally.  A per-process lock and a short Redis lock
prevent concurrent misses from repeating the same calculation across workers.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

import structlog
from fastapi.encoders import jsonable_encoder

from app.services.login_limiter import redis_client

CACHE_PREFIX = "alc:response:v1"
LOCK_SECONDS = 10
LOCK_WAIT_SECONDS = 2.0
MAX_VALUE_BYTES = 2 * 1024 * 1024

log = structlog.get_logger("app.cache")
_local_locks: dict[str, asyncio.Lock] = {}
_invalidation_retries: dict[tuple[str, ...], asyncio.Task] = {}


@dataclass(frozen=True)
class CacheResult:
    value: Any
    status: str  # hit, miss, bypass, or unavailable


def _digest(parts: Iterable[object]) -> str:
    canonical = json.dumps(list(parts), separators=(",", ":"), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _version(client, namespace: str) -> int:
    key = f"{CACHE_PREFIX}:version:{namespace}"
    value = await client.get(key)
    if value is not None:
        return int(value)
    await client.set(key, 1, nx=True)
    return int(await client.get(key) or 1)


async def invalidate(namespaces: Iterable[str], *, reason: str) -> None:
    """Atomically advance cache generations. Redis outages never fail a committed write."""
    names = tuple(sorted(set(namespaces)))
    if not names:
        return
    try:
        client = redis_client()
        async with client.pipeline(transaction=True) as pipe:
            for name in names:
                pipe.incr(f"{CACHE_PREFIX}:version:{name}")
            await pipe.execute()
        log.info("response_cache_invalidated", namespaces=names, reason=reason)
    except Exception as exc:
        log.warning(
            "response_cache_invalidation_unavailable",
            namespaces=names,
            reason=reason,
            error_type=type(exc).__name__,
        )
        _schedule_invalidation_retry(names, reason)


def _schedule_invalidation_retry(names: tuple[str, ...], reason: str) -> None:
    """Keep retrying a failed version bump; official results never depend on TTL alone."""
    existing = _invalidation_retries.get(names)
    if existing is not None and not existing.done():
        return

    async def retry() -> None:
        delay = 0.25
        while True:
            await asyncio.sleep(delay)
            try:
                client = redis_client()
                async with client.pipeline(transaction=True) as pipe:
                    for name in names:
                        pipe.incr(f"{CACHE_PREFIX}:version:{name}")
                    await pipe.execute()
                log.info(
                    "response_cache_invalidation_recovered", namespaces=names, reason=reason
                )
                return
            except Exception:
                delay = min(delay * 2, 5.0)

    task = asyncio.create_task(retry(), name="response-cache-invalidation-retry")
    _invalidation_retries[names] = task
    task.add_done_callback(lambda finished: _invalidation_retries.pop(names, None))


async def _release_lock(client, key: str, token: str) -> None:
    script = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('del', KEYS[1]) else return 0 end"
    )
    await client.eval(script, 1, key, token)


async def get_or_create(
    namespace: str,
    parts: Iterable[object],
    ttl_seconds: int,
    builder: Callable[[], Awaitable[Any]],
    *,
    cacheable: bool = True,
) -> CacheResult:
    """Return a shared JSON value, or safely run ``builder`` when caching is unavailable."""
    if not cacheable:
        return CacheResult(await builder(), "bypass")
    try:
        client = redis_client()
        version = await _version(client, namespace)
        key = f"{CACHE_PREFIX}:data:{namespace}:{version}:{_digest(parts)}"
        cached = await client.get(key)
        if cached is not None:
            log.info("response_cache_hit", namespace=namespace, bytes=len(cached))
            return CacheResult(json.loads(cached), "hit")
    except Exception as exc:
        log.warning(
            "response_cache_unavailable", namespace=namespace, error_type=type(exc).__name__
        )
        return CacheResult(await builder(), "unavailable")

    local_lock = _local_locks.setdefault(key, asyncio.Lock())
    async with local_lock:
        try:
            cached = await client.get(key)
            if cached is not None:
                return CacheResult(json.loads(cached), "hit")
            lock_key = f"{key}:lock"
            token = secrets.token_hex(16)
            owns_lock = bool(await client.set(lock_key, token, nx=True, ex=LOCK_SECONDS))
            if not owns_lock:
                deadline = time.monotonic() + LOCK_WAIT_SECONDS
                while time.monotonic() < deadline:
                    await asyncio.sleep(0.025)
                    cached = await client.get(key)
                    if cached is not None:
                        return CacheResult(json.loads(cached), "hit")
                # A dead/slow lock owner must not make the endpoint unavailable.
                return CacheResult(await builder(), "bypass")
            try:
                value = await builder()
                encoded = json.dumps(
                    jsonable_encoder(value), separators=(",", ":"), ensure_ascii=False
                ).encode()
                if len(encoded) <= MAX_VALUE_BYTES:
                    await client.set(key, encoded, ex=ttl_seconds)
                else:
                    log.warning(
                        "response_cache_value_too_large",
                        namespace=namespace,
                        bytes=len(encoded),
                        limit=MAX_VALUE_BYTES,
                    )
                log.info("response_cache_miss", namespace=namespace, bytes=len(encoded))
                return CacheResult(json.loads(encoded), "miss")
            finally:
                try:
                    await _release_lock(client, lock_key, token)
                except Exception:
                    pass
        except Exception as exc:
            log.warning(
                "response_cache_unavailable", namespace=namespace, error_type=type(exc).__name__
            )
            return CacheResult(await builder(), "unavailable")

