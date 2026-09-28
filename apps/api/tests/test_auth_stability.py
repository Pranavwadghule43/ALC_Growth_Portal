"""Phase 4C authentication stability.

* Refresh-token rotation is atomic: one refresh token yields at most one successor session,
  even when several refresh requests present it at the same moment.
* Argon2 runs off the event loop, through a small bounded pool of worker threads.
* Logins, forced password change and session revocation behave exactly as before.

Everything here uses test fixtures only (no real database or credentials).
"""
import asyncio
import os
import threading
import time
import weakref
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import auth
from app.auth import (
    hash_password,
    hash_password_async,
    new_refresh_token,
    password_limiter,
    verify_password,
    verify_password_async,
)
from app.database import Base, get_db
from app.enums import Role
from app.main import app
from app.models import DCU, RCU, RefreshToken, User
from tests.conftest import login

ALC_A = ("00010001", "StrongAlcPassA!", "ALC")
SBU_4 = ("sbu-4", "StrongSbuPass4!", "SBU")
ADMIN = ("admin", "StrongAdminPass!", "ADMIN")
ARGON2_PARAMS = "$argon2id$v=19$m=65536,t=3,p=4$"  # unchanged hashing parameters


def logout(client):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)


async def refresh_with(client, raw_token):
    """POST /auth/refresh presenting exactly ``raw_token`` (nothing else from the jar)."""
    logout(client)
    return await client.post("/api/auth/refresh", headers={"Cookie": f"refresh_token={raw_token}"})


# --------------------------------------------------------------------------- #
# Atomic refresh-token rotation
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_valid_refresh_token_rotates_and_new_session_works(client, seeded):
    assert (await login(client, *ALC_A)).status_code == 200
    first = client.cookies.get("refresh_token")

    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 200 and resp.json()["role"] == "ALC"
    second = client.cookies.get("refresh_token")
    assert second and second != first
    # The new access token works, and the new refresh token can rotate again.
    assert (await client.get("/api/auth/me")).status_code == 200
    assert (await client.post("/api/auth/refresh")).status_code == 200


@pytest.mark.asyncio
async def test_old_refresh_token_is_rejected_after_rotation(client, seeded):
    await login(client, *ALC_A)
    old = client.cookies.get("refresh_token")
    assert (await client.post("/api/auth/refresh")).status_code == 200
    successor = client.cookies.get("refresh_token")

    for _ in range(2):  # every later use of the old token fails the same safe way
        resp = await refresh_with(client, old)
        assert resp.status_code == 401 and resp.json()["detail"] == "Session expired"
    # Rejecting the old token does not disturb the successor session.
    assert (await refresh_with(client, successor)).status_code == 200


@pytest.mark.asyncio
async def test_expired_refresh_token_is_rejected(client, session, seeded):
    raw, digest = new_refresh_token()
    session.add(
        RefreshToken(
            user_id=seeded["a"].id,
            token_hash=digest,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    )
    await session.commit()
    assert (await refresh_with(client, raw)).status_code == 401


@pytest.fixture
async def isolated_app(tmp_path):
    """The app on its own database with a NEW session per request, like production.

    Uses a throwaway SQLite file by default; set AUTH_RACE_DATABASE_URL to run the race
    against another test database (e.g. an empty local PostgreSQL).
    """
    url = os.environ.get("AUTH_RACE_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp_path}/race.db"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def per_request_db():
        async with maker() as db:
            yield db

    app.dependency_overrides[get_db] = per_request_db
    try:
        yield maker
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_rotation_of_one_token_has_exactly_one_winner(isolated_app):
    async with isolated_app() as db:
        user = User(username="race-admin", password_hash="x", role=Role.ADMIN)
        db.add(user)
        await db.flush()
        raw, digest = new_refresh_token()
        db.add(
            RefreshToken(
                user_id=user.id,
                token_hash=digest,
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        await db.commit()
        user_id = user.id

    async def attempt():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/api/auth/refresh", headers={"Cookie": f"refresh_token={raw}"}
            )
            return resp.status_code, resp.cookies.get("refresh_token")

    results = await asyncio.gather(*(attempt() for _ in range(6)))
    statuses = sorted(status for status, _ in results)
    assert statuses == [200, 401, 401, 401, 401, 401]

    async with isolated_app() as db:
        tokens = list(await db.scalars(select(RefreshToken).where(RefreshToken.user_id == user_id)))
    active = [t for t in tokens if t.revoked_at is None]
    assert len(tokens) == 2 and len(active) == 1  # the original (revoked) + one successor
    winner = next(cookie for status, cookie in results if status == 200)
    assert active[0].token_hash == auth.token_digest(winner)


# --------------------------------------------------------------------------- #
# Session revocation is unchanged
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_password_reset_still_ends_refresh_sessions(client, seeded):
    await login(client, *ALC_A)
    alc_refresh = client.cookies.get("refresh_token")

    logout(client)
    await login(client, *SBU_4)
    resp = await client.post(
        f"/api/portal/alcs/{seeded['alc_a'].id}/reset-password",
        json={"password": "TempResetPass123!"},
    )
    assert resp.status_code == 200, resp.text
    assert (await refresh_with(client, alc_refresh)).status_code == 401


@pytest.mark.asyncio
async def test_self_password_change_keeps_current_session_only(client, seeded):
    await login(client, *ALC_A)
    other_browser = client.cookies.get("refresh_token")
    logout(client)
    await login(client, *ALC_A)
    current = client.cookies.get("refresh_token")

    resp = await client.post(
        "/api/auth/change-password",
        json={"current_password": ALC_A[1], "new_password": "BrandNewAlcPass9!"},
    )
    assert resp.status_code == 200
    assert (await refresh_with(client, other_browser)).status_code == 401
    assert (await refresh_with(client, current)).status_code == 200


# --------------------------------------------------------------------------- #
# Async Argon2 wrapper and its concurrency limit
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_async_verify_matches_sync_verify():
    encoded = hash_password("Correct-Horse-9!")
    for candidate in ("Correct-Horse-9!", "wrong-password", ""):
        assert await verify_password_async(candidate, encoded) == verify_password(
            candidate, encoded
        )
    assert await verify_password_async("anything", "not-a-hash") is False


@pytest.mark.asyncio
async def test_async_hash_is_valid_argon2_with_unchanged_parameters():
    encoded = await hash_password_async("Correct-Horse-9!")
    assert encoded.startswith(ARGON2_PARAMS)
    assert verify_password("Correct-Horse-9!", encoded)
    assert await verify_password_async("Correct-Horse-9", encoded) is False  # wrong password


@pytest.mark.asyncio
async def test_argon2_runs_off_the_event_loop():
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0.01)
    before = ticks
    await asyncio.gather(*(hash_password_async("Correct-Horse-9!") for _ in range(3)))
    task.cancel()
    assert ticks > before  # the loop kept running while Argon2 worked


@pytest.fixture
def limit_of(monkeypatch):
    def apply(limit):
        monkeypatch.setattr(auth.settings, "password_hash_concurrency", limit)
        monkeypatch.setattr(auth, "_limiters", weakref.WeakKeyDictionary())

    return apply


@pytest.mark.asyncio
async def test_limiter_bounds_concurrent_password_operations(monkeypatch, limit_of):
    limit_of(2)
    lock, running, peak = threading.Lock(), [0], [0]

    def slow_hash(password):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.03)
        with lock:
            running[0] -= 1
        return "hashed"

    monkeypatch.setattr(auth, "hash_password", slow_hash)
    results = await asyncio.gather(*(hash_password_async("pw") for _ in range(10)))
    assert results == ["hashed"] * 10
    assert peak[0] == 2
    assert password_limiter().borrowed_tokens == 0


@pytest.mark.asyncio
async def test_failing_operation_releases_its_permit(monkeypatch, limit_of):
    limit_of(1)

    def broken_verify(password, encoded):
        raise RuntimeError("argon2 failure")

    monkeypatch.setattr(auth, "verify_password", broken_verify)
    outcomes = await asyncio.gather(
        *(verify_password_async("pw", "hash") for _ in range(4)), return_exceptions=True
    )
    assert all(isinstance(o, RuntimeError) for o in outcomes)
    assert password_limiter().borrowed_tokens == 0
    monkeypatch.undo()  # real functions back; the single permit is still usable
    limit_of(1)
    assert await verify_password_async("pw", hash_password("pw")) is True


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_leak_a_permit(monkeypatch, limit_of):
    limit_of(1)
    release = threading.Event()

    def blocking_hash(password):
        release.wait(2)
        return "hashed"

    monkeypatch.setattr(auth, "hash_password", blocking_hash)
    holder = asyncio.create_task(hash_password_async("a"))
    await asyncio.sleep(0.02)
    waiter = asyncio.create_task(hash_password_async("b"))  # queued behind the holder
    await asyncio.sleep(0.02)
    waiter.cancel()
    release.set()
    assert await holder == "hashed"
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert password_limiter().borrowed_tokens == 0


# --------------------------------------------------------------------------- #
# Regression: every login type and forced password change
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_alc_sbu_and_admin_logins_still_work(client, seeded):
    for who, role in ((ALC_A, "ALC"), (SBU_4, "SBU"), (ADMIN, "ADMIN")):
        logout(client)
        resp = await login(client, *who)
        assert resp.status_code == 200 and resp.json()["role"] == role
        assert (await client.get("/api/auth/me")).json()["role"] == role
    logout(client)
    bad = await login(client, ALC_A[0], "WrongPassword123!", "ALC")
    assert bad.status_code == 401


@pytest.mark.asyncio
async def test_dcu_login_still_works(client, session, seeded):
    rcu = RCU(code="RCU_TEST", name="Test RCU")
    session.add(rcu)
    await session.flush()
    dcu = DCU(code="DCU_TEST", name="Test DCU", rcu_id=rcu.id)
    session.add(dcu)
    await session.flush()
    session.add(
        User(
            username="dcu-test",
            password_hash=hash_password("StrongDcuPass123!"),
            role=Role.DCU,
            dcu_id=dcu.id,
            must_change_password=False,
        )
    )
    await session.commit()
    resp = await login(client, "dcu-test", "StrongDcuPass123!", "DCU")
    assert resp.status_code == 200 and resp.json()["role"] == "DCU"


@pytest.mark.asyncio
async def test_forced_password_change_still_works(client, session, seeded):
    user = seeded["a"]
    user.must_change_password = True
    await session.commit()

    await login(client, *ALC_A)
    blocked = await client.get("/api/portal/dashboard")
    assert blocked.status_code == 403 and blocked.json()["detail"] == "Password change required"
    same = await client.post(
        "/api/auth/change-password",
        json={"current_password": ALC_A[1], "new_password": ALC_A[1]},
    )
    assert same.status_code == 400
    changed = await client.post(
        "/api/auth/change-password",
        json={"current_password": ALC_A[1], "new_password": "BrandNewAlcPass9!"},
    )
    assert changed.status_code == 200
    assert (await client.get("/api/portal/dashboard")).status_code == 200

    await session.refresh(user)
    assert user.must_change_password is False
    assert user.password_hash.startswith(ARGON2_PARAMS)
    active = await session.scalar(
        select(func.count(RefreshToken.id)).where(
            RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
        )
    )
    assert active == 1