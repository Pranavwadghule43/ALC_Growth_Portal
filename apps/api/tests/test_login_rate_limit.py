"""Phase 4G-B: login rate limiting + trusted-proxy client IP.

* Client-IP limit (every attempt) and client-IP + identifier failure limit, per scope
  ("portal" /auth/login, "admin" /auth/admin-login), both BEFORE any Argon2 work.
* No account lockout: another client IP can still sign in to the same account.
* Redis keys never contain the raw identifier; counters are atomic and always expire.
* Forwarding headers are believed only from TRUSTED_PROXY_CIDRS peers, walked from the right.
* Redis outage -> bounded process-local fallback, never a 500.

Uses fake clocks and fake client IPs; no wall-clock sleeps. Tests marked "real Redis" run
only when LOGIN_RATE_LIMIT_TEST_REDIS_URL points at a disposable Redis database.
"""
import asyncio
import os
import re
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from app.auth import DUMMY_PASSWORD_HASH
from app.main import app
from app.routes import auth as auth_routes
from app.services import login_limiter
from app.services.login_limiter import (
    THROTTLED_DETAIL,
    LocalBackend,
    RedisBackend,
    client_ip,
    identifier_digest,
    pair_key,
)
from tests.conftest import login

ALC_A = ("00010001", "StrongAlcPassA!", "ALC")
SBU_4 = ("sbu-4", "StrongSbuPass4!", "SBU")
ADMIN = ("admin", "StrongAdminPass!", "ADMIN")
WRONG = "WrongPassword123!"
PORTAL_INVALID = "Invalid username/ALC code or password"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(login_limiter, "_local", LocalBackend(10_000, clock=fake))
    return fake


@pytest.fixture
def limits(monkeypatch):
    def apply(ip=100, pair=8):
        monkeypatch.setattr(login_limiter.settings, "login_ip_limit", ip)
        monkeypatch.setattr(login_limiter.settings, "login_pair_failure_limit", pair)

    return apply


@pytest.fixture
def trusted(monkeypatch):
    def apply(cidrs: str):
        monkeypatch.setattr(login_limiter.settings, "trusted_proxy_cidrs", cidrs)

    return apply


@pytest.fixture
def verify_calls(monkeypatch):
    calls: list[str] = []
    real = auth_routes.verify_password_async

    async def spy(password, encoded):
        calls.append(encoded)
        return await real(password, encoded)

    monkeypatch.setattr(auth_routes, "verify_password_async", spy)
    return calls


def make_request(peer: str | None, headers: dict[str, str] | list | None = None) -> Request:
    items = headers.items() if isinstance(headers, dict) else (headers or [])
    return Request({
        "type": "http", "method": "POST", "path": "/api/auth/login",
        "client": (peer, 1234) if peer else None,
        "headers": [(k.lower().encode(), v.encode()) for k, v in items],
    })


def from_ip(ip: str) -> AsyncClient:
    """A browser connecting from ``ip`` (the test DB override applies to the app)."""
    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 5000)), base_url="http://t")


async def attempt(client, who, password=None):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    identifier, real_password, portal = who
    return await login(client, identifier, password or real_password, portal)


# --------------------------------------------------------------------------- #
# Client IP and trusted proxies
# --------------------------------------------------------------------------- #
SPOOF = {"X-Forwarded-For": "1.2.3.4", "X-Real-IP": "5.6.7.8", "Forwarded": "for=9.9.9.9"}


def test_untrusted_peer_ignores_all_forwarding_headers(trusted):
    trusted("")
    assert client_ip(make_request("203.0.113.10", SPOOF)) == "203.0.113.10"
    for name, value in SPOOF.items():
        assert client_ip(make_request("203.0.113.10", {name: value})) == "203.0.113.10"
    trusted("127.0.0.1/32")  # trusting a different proxy does not change this peer
    assert client_ip(make_request("203.0.113.10", SPOOF)) == "203.0.113.10"


def test_private_ranges_are_not_trusted_by_default(trusted):
    trusted("")
    for peer in ("10.0.0.5", "172.16.3.4", "192.168.1.9", "127.0.0.1"):
        assert client_ip(make_request(peer, {"X-Forwarded-For": "1.2.3.4"})) == peer


def test_trusted_proxy_honours_forwarded_client(trusted):
    trusted("127.0.0.1/32")
    assert client_ip(make_request("127.0.0.1", {"X-Forwarded-For": "198.51.100.7"})) == \
        "198.51.100.7"
    assert client_ip(make_request("127.0.0.1", {"X-Real-IP": "198.51.100.8"})) == "198.51.100.8"
    assert client_ip(make_request("127.0.0.1", {"Forwarded": 'for="[2001:db8::7]:4711"'})) == \
        "2001:db8::7"
    assert client_ip(make_request("127.0.0.1", {})) == "127.0.0.1"  # no header: the peer


def test_multi_hop_chain_is_walked_from_the_right(trusted):
    trusted("127.0.0.1/32, 10.0.0.0/24")
    # attacker-chosen "1.1.1.1" on the left is ignored; the first untrusted hop wins
    headers = {"X-Forwarded-For": "1.1.1.1, 203.0.113.9, 10.0.0.2"}
    assert client_ip(make_request("127.0.0.1", headers)) == "203.0.113.9"
    # the same chain split over several header lines
    split = [("X-Forwarded-For", "1.1.1.1"), ("X-Forwarded-For", "203.0.113.9, 10.0.0.2")]
    assert client_ip(make_request("127.0.0.1", split)) == "203.0.113.9"
    # every hop trusted -> the leftmost (an internal client)
    assert client_ip(make_request("127.0.0.1", {"X-Forwarded-For": "10.0.0.8, 10.0.0.2"})) == \
        "10.0.0.8"
    # ports and IPv4-mapped IPv6 are normalized
    assert client_ip(make_request("127.0.0.1", {"X-Forwarded-For": "198.51.100.3:5555"})) == \
        "198.51.100.3"
    assert client_ip(make_request("::ffff:127.0.0.1", {"X-Forwarded-For": "::ffff:1.2.3.4"})) == \
        "1.2.3.4"


@pytest.mark.parametrize(
    "value", ["garbage", "unknown", "_hidden", "[2001:db8::1", "1.2.3.4.5", "", " , ", "999.1.1.1"]
)
def test_malformed_forwarded_values_fall_back_to_the_peer(trusted, value):
    trusted("127.0.0.1/32")
    assert client_ip(make_request("127.0.0.1", {"X-Forwarded-For": value})) == "127.0.0.1"
    assert client_ip(make_request("127.0.0.1", {"Forwarded": f"for={value}"})) == "127.0.0.1"


@pytest.mark.asyncio
async def test_malformed_forwarded_header_does_not_crash_login(client, seeded, trusted):
    trusted("127.0.0.1/32")
    resp = await client.post(
        "/api/auth/login",
        json={"identifier": "nobody-here", "password": WRONG},
        headers={"X-Forwarded-For": "\x7f\x00garbage,,[]:::", "Forwarded": "for=;;;=="},
    )
    assert resp.status_code == 401 and resp.json()["detail"] == PORTAL_INVALID


def test_invalid_trusted_proxy_config_is_rejected():
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(trusted_proxy_cidrs="127.0.0.1/32, not-a-network")


# --------------------------------------------------------------------------- #
# Limits over HTTP
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_ip_limit_applies_across_identifiers(client, seeded, limits):
    limits(ip=5)
    for n in range(5):
        resp = await attempt(client, (f"unknown-{n}", WRONG, "SBU"))
        assert resp.status_code == 401
    resp = await attempt(client, ("unknown-99", WRONG, "SBU"))
    assert resp.status_code == 429 and resp.json() == {"detail": THROTTLED_DETAIL}
    # even the correct password is refused from this IP until the window ends
    assert (await attempt(client, ALC_A)).status_code == 429


@pytest.mark.asyncio
async def test_pair_limit_same_ip_and_identifier(client, seeded, limits):
    limits(pair=3)
    for _ in range(3):
        assert (await attempt(client, SBU_4, WRONG)).status_code == 401
    blocked = await attempt(client, SBU_4, WRONG)
    assert blocked.status_code == 429 and blocked.json() == {"detail": THROTTLED_DETAIL}
    assert (await attempt(client, SBU_4)).status_code == 429  # correct password too


@pytest.mark.asyncio
async def test_pair_limit_is_isolated_by_identifier(client, seeded, limits):
    limits(pair=3)
    for _ in range(3):
        await attempt(client, SBU_4, WRONG)
    assert (await attempt(client, SBU_4, WRONG)).status_code == 429
    assert (await attempt(client, ALC_A)).status_code == 200  # another identifier, same IP


@pytest.mark.asyncio
async def test_pair_limit_is_isolated_by_client_ip_no_account_lockout(client, seeded, limits):
    limits(pair=3)
    async with from_ip("198.51.100.66") as attacker:
        for _ in range(3):
            await attempt(attacker, ALC_A, WRONG)
        assert (await attempt(attacker, ALC_A, WRONG)).status_code == 429
    async with from_ip("203.0.113.20") as owner:  # the real user elsewhere is unaffected
        ok = await attempt(owner, ALC_A)
        assert ok.status_code == 200 and ok.json()["role"] == "ALC"


@pytest.mark.asyncio
async def test_many_users_behind_one_nat_are_not_limited(client, session, seeded):
    # 30 people in one office (one public IP), each mistyping once and then signing in.
    from app.auth import hash_password
    from app.enums import Role
    from app.models import User

    password_hash = hash_password("OfficePassword1!")
    for n in range(30):
        session.add(User(username=f"office-{n}", password_hash=password_hash, role=Role.SBU,
                         sbu_id=seeded["sbu4"].id, must_change_password=False))
    await session.commit()
    async with from_ip("192.0.2.50") as office:
        for n in range(30):
            assert (await attempt(office, (f"office-{n}", WRONG, "SBU"))).status_code == 401
            assert (await attempt(office, (f"office-{n}", "OfficePassword1!", "SBU"))).status_code \
                == 200


@pytest.mark.asyncio
async def test_admin_and_portal_scopes_are_independent(client, seeded, limits):
    limits(ip=3, pair=2)
    for _ in range(2):
        await attempt(client, ADMIN, WRONG)
    assert (await attempt(client, ADMIN, WRONG)).status_code == 429       # admin pair used up
    assert (await attempt(client, ("admin", WRONG, "SBU"))).status_code == 401  # portal fresh
    # portal IP counter is separate from the admin one
    assert (await attempt(client, ("x-1", WRONG, "SBU"))).status_code == 401
    assert (await attempt(client, ("x-2", WRONG, "SBU"))).status_code == 401  # 3rd portal try
    assert (await attempt(client, ("x-3", WRONG, "SBU"))).status_code == 429  # 4th: over limit
    assert (await attempt(client, ADMIN)).status_code == 429  # admin IP: 3 used + this one


@pytest.mark.asyncio
async def test_successful_login_clears_pair_failures(client, seeded, limits):
    limits(pair=3)
    for _ in range(2):
        await attempt(client, SBU_4, WRONG)
    assert (await attempt(client, SBU_4)).status_code == 200
    for _ in range(3):  # a fresh budget of 3 failures
        assert (await attempt(client, SBU_4, WRONG)).status_code == 401
    assert (await attempt(client, SBU_4, WRONG)).status_code == 429


@pytest.mark.asyncio
async def test_account_unavailable_is_not_a_credential_failure(client, session, seeded, limits):
    limits(pair=3)
    seeded["sbu4"].is_active = False  # Centre A's SBU
    await session.commit()
    for _ in range(6):
        resp = await attempt(client, ALC_A)
        assert resp.status_code == 401 and resp.json()["detail"] == "Account unavailable"
    key = pair_key("portal", "127.0.0.1", ALC_A[0].lower())
    assert key not in login_limiter._local._entries or login_limiter._local._entries[key][0] == 0


# --------------------------------------------------------------------------- #
# Keys never hold raw identifiers
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_rate_limit_keys_hold_no_raw_identifiers(client, session, seeded):
    seeded["sbu4_user"].email = "sbu4.owner@example.com"
    await session.commit()
    identifiers = ["sbu-4", "sbu4.owner@example.com", "00010001", "no-such-user@corp.example"]
    for identifier in identifiers:
        await attempt(client, (identifier, WRONG, "SBU"))
        await attempt(client, (identifier, WRONG, "ADMIN"))
    keys = list(login_limiter._local._entries)
    assert keys
    pattern = re.compile(
        r"^login:v1:(portal|admin):(ip:[0-9a-f:.]+|pair:[0-9a-f:.]+:[0-9a-f]{32})$"
    )
    for key in keys:
        assert pattern.match(key), key
        for identifier in identifiers:
            assert identifier not in key and identifier.upper() not in key
    assert identifier_digest("sbu-4") != identifier_digest("sbu-5")
    assert identifier_digest("sbu-4") == identifier_digest("sbu-4")


@pytest.mark.asyncio
async def test_redis_backend_receives_hashed_keys_and_expiry():
    class Recorder:
        def __init__(self):
            self.calls = []

        async def eval(self, script, numkeys, *args):
            self.calls.append(args)
            return [1, 5000]

        async def delete(self, key):
            self.calls.append((key,))

    rec = Recorder()
    backend = RedisBackend(rec)
    key = pair_key("portal", "203.0.113.5", "ops@example.com")
    await backend.hit(key, 8, 900)
    await backend.reserve(key, 8, 900)
    await backend.refund(key)
    await backend.clear(key)
    assert all("ops@example.com" not in str(call) for call in rec.calls)
    assert rec.calls[0] == (key, 900_000) and rec.calls[1] == (key, 900_000, 8)


# --------------------------------------------------------------------------- #
# Argon2 work: none when throttled, exactly one otherwise (Phase 4G-A intact)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_argon2_counts_with_and_without_throttle(client, seeded, limits, verify_calls):
    limits(pair=2)
    await attempt(client, ("ghost-user", WRONG, "SBU"))
    assert verify_calls == [DUMMY_PASSWORD_HASH]                   # unknown: 1 dummy
    verify_calls.clear()
    await attempt(client, SBU_4, WRONG)
    assert verify_calls == [seeded["sbu4_user"].password_hash]     # real: 1 real
    await attempt(client, SBU_4, WRONG)
    verify_calls.clear()
    throttled = await attempt(client, SBU_4, WRONG)
    assert throttled.status_code == 429 and verify_calls == []     # throttled: 0
    await attempt(client, ADMIN, WRONG)
    await attempt(client, ADMIN, WRONG)
    verify_calls.clear()
    assert (await attempt(client, ADMIN, WRONG)).status_code == 429
    assert verify_calls == []


# --------------------------------------------------------------------------- #
# 429 response and Retry-After (fake clock, no sleeping)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_429_is_generic_with_accurate_retry_after(client, seeded, limits, clock):
    limits(pair=2)
    for _ in range(2):
        await attempt(client, ("maybe-missing", WRONG, "SBU"))
    clock.now += 100
    resp = await attempt(client, ("maybe-missing", WRONG, "SBU"))
    assert resp.status_code == 429 and resp.json() == {"detail": THROTTLED_DETAIL}
    assert resp.headers["Retry-After"] == "800"  # 900 s window, 100 s elapsed
    # same response for an existing account: nothing about existence or status leaks
    for _ in range(2):
        await attempt(client, SBU_4, WRONG)
    other = await attempt(client, SBU_4, WRONG)
    assert other.json() == resp.json()
    clock.now += 801  # the window has passed: allowed again
    assert (await attempt(client, ("maybe-missing", WRONG, "SBU"))).status_code == 401


@pytest.mark.asyncio
async def test_ip_limit_retry_after(client, seeded, limits, clock):
    limits(ip=1)
    await attempt(client, ("a-user", WRONG, "SBU"))
    clock.now += 42
    resp = await attempt(client, ("b-user", WRONG, "SBU"))
    assert resp.status_code == 429 and resp.headers["Retry-After"] == str(600 - 42)


# --------------------------------------------------------------------------- #
# Redis outage -> local fallback, bounded
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_redis_outage_uses_fallback_and_never_500(client, seeded, limits, monkeypatch):
    class DeadRedis:
        async def eval(self, *args):
            raise ConnectionError("redis down")

        async def delete(self, *args):
            raise ConnectionError("redis down")

    monkeypatch.setattr(login_limiter, "_redis_down_until", 0.0)
    monkeypatch.setattr(login_limiter, "_redis_backend", lambda: RedisBackend(DeadRedis()))
    limits(pair=2)
    assert (await attempt(client, SBU_4, WRONG)).status_code == 401
    assert login_limiter._redis_down_until > 0  # Redis is skipped briefly after a failure
    assert (await attempt(client, SBU_4, WRONG)).status_code == 401
    assert (await attempt(client, SBU_4, WRONG)).status_code == 429  # fallback still limits
    assert (await attempt(client, ALC_A)).status_code == 200


def test_local_fallback_is_bounded_and_drops_expired_first():
    fake = FakeClock()
    backend = LocalBackend(100, clock=fake)

    async def fill():
        for n in range(50):
            await backend.hit(f"old-{n}", 10, 10)
        fake.now += 11  # those 50 expire
        for n in range(500):
            await backend.hit(f"new-{n}", 10, 600)

    asyncio.run(fill())
    assert len(backend) == 100
    assert not any(key.startswith("old-") for key in backend._entries)
    assert "new-499" in backend._entries  # most recent kept, oldest evicted


# --------------------------------------------------------------------------- #
# Concurrency cannot bypass the limit
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_concurrent_reservations_cannot_exceed_limit():
    backend = LocalBackend(1000)
    results = await asyncio.gather(*(backend.reserve("k", 8, 900) for _ in range(50)))
    assert sum(r.allowed for r in results) == 8
    hits = await asyncio.gather(*(backend.hit("ip", 20, 600) for _ in range(50)))
    assert sum(h.allowed for h in hits) == 20


REDIS_URL = os.environ.get("LOGIN_RATE_LIMIT_TEST_REDIS_URL")
real_redis = pytest.mark.skipif(not REDIS_URL, reason="set LOGIN_RATE_LIMIT_TEST_REDIS_URL")


@real_redis
@pytest.mark.asyncio
async def test_real_redis_atomic_limits_and_expiry():
    from redis.asyncio import Redis

    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    backend = RedisBackend(redis)
    prefix = f"login:v1:test:{uuid.uuid4().hex}"
    try:
        # 50 concurrent reservations at limit 8: exactly 8 succeed
        results = await asyncio.gather(*(backend.reserve(f"{prefix}:pair", 8, 900)
                                         for _ in range(50)))
        assert sum(r.allowed for r in results) == 8
        blocked = await backend.reserve(f"{prefix}:pair", 8, 900)
        assert not blocked.allowed and 890 <= blocked.retry_after <= 900
        # 50 concurrent hits at limit 20: exactly 20 allowed; the key always has a TTL
        hits = await asyncio.gather(*(backend.hit(f"{prefix}:ip", 20, 600) for _ in range(50)))
        assert sum(h.allowed for h in hits) == 20
        assert 0 < await redis.pttl(f"{prefix}:ip") <= 600_000
        # a key that lost its expiry is repaired on the next hit
        await redis.set(f"{prefix}:stale", 3)
        await backend.hit(f"{prefix}:stale", 20, 600)
        assert await redis.pttl(f"{prefix}:stale") > 0
        # refund never goes below zero; clear removes the counter
        for _ in range(20):
            await backend.refund(f"{prefix}:pair")
        assert int(await redis.get(f"{prefix}:pair")) == 0
        await backend.clear(f"{prefix}:pair")
        assert await redis.exists(f"{prefix}:pair") == 0
    finally:
        keys = [k async for k in redis.scan_iter(f"{prefix}:*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


# --------------------------------------------------------------------------- #
# Regression: session behaviour unchanged
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_must_change_password_and_session_unchanged(client, session, seeded):
    seeded["a"].must_change_password = True
    await session.commit()
    resp = await attempt(client, ALC_A)
    assert resp.status_code == 200 and resp.json()["must_change_password"] is True
    assert {"access_token", "refresh_token", "csrf_token"} <= set(resp.cookies.keys())
    blocked = await client.get("/api/portal/dashboard")
    assert blocked.status_code == 403 and blocked.json()["detail"] == "Password change required"
    assert (await client.post("/api/auth/refresh")).status_code == 200
    assert (await client.get("/api/auth/me")).json()["role"] == "ALC"
