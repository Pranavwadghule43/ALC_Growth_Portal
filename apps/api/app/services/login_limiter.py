"""Login rate limiting (Phase 4G-B).

Two independent limits per login endpoint ("portal" = POST /auth/login, "admin" =
POST /auth/admin-login), checked BEFORE any Argon2 work:

* Client-IP limit: every attempt from one client IP counts (default 100 per 10 minutes).
  Deliberately generous, because many genuine users can share one office/NAT address.
* Client-IP + identifier failure limit: failed attempts for the same client IP and the
  same normalized identifier (default 8 per 15 minutes). An attempt reserves a slot
  atomically before the password check; the slot is refunded when the credentials turn
  out to be valid (success, or Phase 4E "Account unavailable"), and the whole counter is
  cleared on a successful login. There is no account-wide lockout: another client IP can
  still sign in to the same account.

Redis keys (never contain the raw username / email / ALC code):

    login:v1:<scope>:ip:<client ip>
    login:v1:<scope>:pair:<client ip>:<hmac-sha256(secret_key, identifier)[:32]>

Every counter change is one atomic Lua script, and every key always has an expiry. If Redis
is unreachable, a bounded process-local fallback with the same rules takes over (per
process only); Redis stays authoritative whenever it is available.

Client IP: request.client.host, unless the connecting peer is inside TRUSTED_PROXY_CIDRS;
only then are X-Forwarded-For / Forwarded / X-Real-IP read, from the right, skipping
trusted proxy hops.

This module is the ONLY trusted-proxy implementation. It relies on request.client being the
direct TCP peer, so Uvicorn must run with --no-proxy-headers (its default proxy-header
processing trusts 127.0.0.1 and would rewrite request.client before this code runs). See
README "Reverse proxy and client IP".
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import math
import threading
import time
import weakref
from collections import OrderedDict
from dataclasses import dataclass

from fastapi import HTTPException, Request
from redis.asyncio import Redis

from app.config import settings

THROTTLED_DETAIL = "Too many login attempts. Try again later."
KEY_PREFIX = "login:v1"
REDIS_RETRY_AFTER_FAILURE_SECONDS = 5.0  # skip Redis briefly after it fails (no stalls)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


# --------------------------------------------------------------------------- #
# Client IP behind trusted proxies
# --------------------------------------------------------------------------- #
def _parse_ip(value: str | None) -> IPAddress | None:
    """An IP from a header/peer value (strips quotes, [brackets] and :port); else None."""
    if not value:
        return None
    text = value.strip().strip('"').strip()
    if text.startswith("["):  # "[2001:db8::1]:443" or "[2001:db8::1]"
        end = text.find("]")
        if end == -1:
            return None
        text = text[1:end]
    elif text.count(":") == 1:  # "203.0.113.7:5678"
        text = text.split(":", 1)[0]
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def _forwarded_chain(request: Request) -> list[str]:
    """Hop values, left (original client) to right (nearest proxy), from the first
    forwarding header present: X-Forwarded-For, then Forwarded (for=), then X-Real-IP."""
    xff = request.headers.getlist("x-forwarded-for")
    if xff:
        return [part for value in xff for part in value.split(",")]
    forwarded = request.headers.getlist("forwarded")
    if forwarded:
        hops = []
        for value in forwarded:
            for element in value.split(","):
                for pair in element.split(";"):
                    name, _, raw = pair.partition("=")
                    if name.strip().lower() == "for":
                        hops.append(raw)
        return hops
    real_ip = request.headers.get("x-real-ip")
    return [real_ip] if real_ip else []


def _is_trusted(ip: IPAddress) -> bool:
    return any(ip in network for network in settings.trusted_proxy_networks)


def client_ip(request: Request) -> str:
    """The client IP used for rate limiting.

    Forwarding headers are ignored unless the direct peer is a configured trusted proxy.
    Then the chain is walked from the right, trusted proxy hops are skipped, and the first
    untrusted address is the client. Anything malformed falls back to the direct peer,
    which can never be spoofed by the client.
    """
    peer_raw = request.client.host if request.client else None
    peer = _parse_ip(peer_raw)
    if peer is None:
        return peer_raw or "unknown"
    if not _is_trusted(peer):
        return str(peer)
    chain = _forwarded_chain(request)
    if not chain:
        return str(peer)
    leftmost = peer
    for raw in reversed(chain):
        hop = _parse_ip(raw)
        if hop is None:
            return str(peer)  # malformed hop: fail safe to the trusted peer
        if not _is_trusted(hop):
            return str(hop)
        leftmost = hop
    return str(leftmost)  # every hop was a trusted proxy


def identifier_digest(identifier: str) -> str:
    """Keyed digest of the already-normalized login identifier (never stored in clear)."""
    mac = hmac.new(
        settings.secret_key.encode(), f"login-identifier:{identifier}".encode(), hashlib.sha256
    )
    return mac.hexdigest()[:32]


def ip_key(scope: str, ip: str) -> str:
    return f"{KEY_PREFIX}:{scope}:ip:{ip}"


def pair_key(scope: str, ip: str, identifier: str) -> str:
    return f"{KEY_PREFIX}:{scope}:pair:{ip}:{identifier_digest(identifier)}"


# --------------------------------------------------------------------------- #
# Counter backends (same semantics: fixed windows, TTL always set)
# --------------------------------------------------------------------------- #
@dataclass
class Hit:
    allowed: bool
    retry_after: int | None  # whole seconds until the window resets (when known)


def _retry_seconds(ms: float) -> int | None:
    return max(1, math.ceil(ms / 1000)) if ms > 0 else None


# HIT: count one attempt; returns {count, pttl}. Sets the expiry on the first hit and
# repairs a key that somehow has none, so a counter can never live forever.
_HIT_LUA = """
local c = redis.call('INCR', KEYS[1])
if c == 1 or redis.call('PTTL', KEYS[1]) < 0 then
  redis.call('PEXPIRE', KEYS[1], ARGV[1])
end
return {c, redis.call('PTTL', KEYS[1])}
"""
# RESERVE: take a failure slot only if fewer than ARGV[2] are used; returns {ok, pttl}.
_RESERVE_LUA = """
local c = tonumber(redis.call('GET', KEYS[1]) or '0')
if c >= tonumber(ARGV[2]) then
  return {0, redis.call('PTTL', KEYS[1])}
end
c = redis.call('INCR', KEYS[1])
if c == 1 or redis.call('PTTL', KEYS[1]) < 0 then
  redis.call('PEXPIRE', KEYS[1], ARGV[1])
end
return {1, redis.call('PTTL', KEYS[1])}
"""
# REFUND: give back one reserved slot (never below zero).
_REFUND_LUA = """
local c = tonumber(redis.call('GET', KEYS[1]) or '0')
if c > 0 then redis.call('DECR', KEYS[1]) end
return 1
"""


class RedisBackend:
    def __init__(self, client) -> None:
        self.client = client

    async def hit(self, key: str, limit: int, window: int) -> Hit:
        count, pttl = await self.client.eval(_HIT_LUA, 1, key, window * 1000)
        allowed = int(count) <= limit
        return Hit(allowed, None if allowed else _retry_seconds(int(pttl)))

    async def reserve(self, key: str, limit: int, window: int) -> Hit:
        ok, pttl = await self.client.eval(_RESERVE_LUA, 1, key, window * 1000, limit)
        allowed = int(ok) == 1
        return Hit(allowed, None if allowed else _retry_seconds(int(pttl)))

    async def refund(self, key: str) -> None:
        await self.client.eval(_REFUND_LUA, 1, key)

    async def clear(self, key: str) -> None:
        await self.client.delete(key)


class LocalBackend:
    """Process-local fallback with the same rules, used only while Redis is unreachable.

    Bounded: at most ``max_keys`` counters; expired ones are dropped first, then the least
    recently used. Protection is per process (each API worker counts on its own).
    """

    def __init__(self, max_keys: int, clock=time.monotonic) -> None:
        self.max_keys = max_keys
        self.clock = clock
        self._entries: OrderedDict[str, list[float]] = OrderedDict()  # key -> [count, expires]
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._entries)

    def _live(self, key: str, now: float) -> list[float] | None:
        entry = self._entries.get(key)
        if entry is not None and entry[1] <= now:
            del self._entries[key]
            return None
        return entry

    def _bump(self, key: str, window: int, now: float) -> list[float]:
        entry = self._live(key, now)
        if entry is None:
            entry = [0, now + window]
            self._entries[key] = entry
            self._shrink(now)
        self._entries.move_to_end(key)
        entry[0] += 1
        return entry

    def _shrink(self, now: float) -> None:
        if len(self._entries) <= self.max_keys:
            return
        for key in [k for k, (_, expires) in self._entries.items() if expires <= now]:
            del self._entries[key]
        while len(self._entries) > self.max_keys:
            self._entries.popitem(last=False)

    async def hit(self, key: str, limit: int, window: int) -> Hit:
        with self._lock:
            now = self.clock()
            count, expires = self._bump(key, window, now)
            allowed = count <= limit
            return Hit(allowed, None if allowed else _retry_seconds((expires - now) * 1000))

    async def reserve(self, key: str, limit: int, window: int) -> Hit:
        with self._lock:
            now = self.clock()
            entry = self._live(key, now)
            if entry is not None and entry[0] >= limit:
                return Hit(False, _retry_seconds((entry[1] - now) * 1000))
            self._bump(key, window, now)
            return Hit(True, None)

    async def refund(self, key: str) -> None:
        with self._lock:
            entry = self._live(key, self.clock())
            if entry is not None and entry[0] > 0:
                entry[0] -= 1

    async def clear(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)


# --------------------------------------------------------------------------- #
# Backend selection: Redis when reachable, else the local fallback
# --------------------------------------------------------------------------- #
_redis_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Redis]" = (
    weakref.WeakKeyDictionary()
)
_redis_down_until = 0.0
_local = LocalBackend(settings.login_fallback_max_keys)


def local_backend() -> LocalBackend:
    return _local


def _redis_backend() -> RedisBackend:
    loop = asyncio.get_running_loop()
    client = _redis_clients.get(loop)
    if client is None:
        client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        _redis_clients[loop] = client
    return RedisBackend(client)


async def _run(operation: str, *args):
    """Run one counter operation on Redis, or on the local fallback if Redis fails."""
    global _redis_down_until
    if time.monotonic() >= _redis_down_until:
        try:
            return await getattr(_redis_backend(), operation)(*args)
        except Exception:  # Redis unreachable/erroring: never a 500 because of it
            _redis_down_until = time.monotonic() + REDIS_RETRY_AFTER_FAILURE_SECONDS
    return await getattr(_local, operation)(*args)


def _throttled(retry_after: int | None) -> HTTPException:
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    return HTTPException(status_code=429, detail=THROTTLED_DETAIL, headers=headers)


# --------------------------------------------------------------------------- #
# The login-route API
# --------------------------------------------------------------------------- #
@dataclass
class LoginAttempt:
    scope: str
    pair: str

    async def credentials_valid(self) -> None:
        """Correct password but no session (Phase 4E "Account unavailable"): not a
        credential failure, so the reserved slot is given back."""
        await _run("refund", self.pair)

    async def succeeded(self) -> None:
        """Successful login: clear this client IP + identifier failure counter."""
        await _run("clear", self.pair)


async def begin_login_attempt(request: Request, scope: str, identifier: str) -> LoginAttempt:
    """Apply both limits before any password work; raise 429 when either is exceeded.

    ``identifier`` must already be normalized exactly as the login query uses it. A slot on
    the IP + identifier counter is reserved now; it stays used if the attempt fails.
    """
    ip = client_ip(request)
    hit = await _run(
        "hit", ip_key(scope, ip), settings.login_ip_limit, settings.login_ip_window_seconds
    )
    if not hit.allowed:
        raise _throttled(hit.retry_after)
    pair = pair_key(scope, ip, identifier)
    reserved = await _run(
        "reserve",
        pair,
        settings.login_pair_failure_limit,
        settings.login_pair_failure_window_seconds,
    )
    if not reserved.allowed:
        raise _throttled(reserved.retry_after)
    return LoginAttempt(scope, pair)
