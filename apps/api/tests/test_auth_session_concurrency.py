"""Real lock/transaction tests in unique schemas of an explicitly disposable PostgreSQL."""
import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.auth import hash_password
from app.database import Base, get_db
from app.dependencies import get_current_user
from app.main import app
from app.models import AuthSession, RefreshToken, User
from app.routes import auth as routes

URL = os.environ.get("ALC_TEST_POSTGRES_URL")
SAFE = bool(URL and os.environ.get("ALC_ISOLATED_TEST") == "1"
            and make_url(URL).host in {"127.0.0.1", "localhost"}
            and "test" in (make_url(URL).database or "").lower())
pytestmark = pytest.mark.skipif(not SAFE, reason="explicit disposable loopback PostgreSQL required")
PASSWORD = "SyntheticRacePassword1!"
NEW = "SyntheticRaceNewPassword2!"


@pytest_asyncio.fixture
async def world():
    schema = f"auth_race_test_{uuid.uuid4().hex}"
    admin = create_async_engine(URL, hide_parameters=True)
    engine = create_async_engine(URL, hide_parameters=True,
        connect_args={"server_settings": {"search_path": schema}})
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    previous = app.dependency_overrides.get(get_db)

    async def request_db():
        async with maker() as db:
            yield db

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with maker() as db:
            encoded = hash_password(PASSWORD)
            target = User(username="race-target", role="ADMIN", password_hash=encoded)
            issuer = User(username="race-issuer", role="ADMIN", password_hash=encoded)
            db.add_all([target, issuer])
            await db.commit()
            target_id = target.id
        app.dependency_overrides[get_db] = request_db
        yield maker, target_id, schema
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def call(method, path, credentials=None, **kwargs):
    headers = {}
    if credentials:
        headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in credentials.items())
        headers["X-CSRF-Token"] = credentials["csrf_token"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, "/api" + path, headers=headers, **kwargs)


async def sign_in(name="race-target"):
    response = await call("POST", "/auth/admin-login", json={
        "identifier": name, "password": PASSWORD})
    assert response.status_code == 200
    return dict(response.cookies)


async def rejected(credentials):
    assert (await call("GET", "/auth/me", credentials)).status_code == 401
    assert (await call("POST", "/auth/refresh", credentials)).status_code == 401


async def test_two_refreshes_have_one_winner_but_replay_contains_entire_family(world):
    maker, _, _ = world
    credentials = await sign_in()
    results = await asyncio.gather(*(call("POST", "/auth/refresh", credentials) for _ in range(2)))
    assert sorted(r.status_code for r in results) == [200, 401]
    winner = next(r for r in results if r.status_code == 200)
    await rejected(dict(winner.cookies))
    async with maker() as db:
        assert await db.scalar(select(func.count(AuthSession.id)).where(
            AuthSession.revoked_at.is_(None))) == 0
        assert await db.scalar(select(func.count(RefreshToken.id)).where(
            RefreshToken.revoked_at.is_(None))) == 0


async def test_refresh_vs_logout_cannot_leave_surviving_successor(world):
    credentials = await sign_in()
    refreshed, logged_out = await asyncio.gather(
        call("POST", "/auth/refresh", credentials), call("POST", "/auth/logout", credentials))
    assert logged_out.status_code == 200
    assert refreshed.status_code in {200, 401}
    await rejected(credentials)
    if refreshed.status_code == 200:
        await rejected(dict(refreshed.cookies))


async def test_refresh_vs_admin_reset_revokes_all_old_credentials(world):
    _, target_id, _ = world
    credentials, issuer = await sign_in(), await sign_in("race-issuer")
    refreshed, reset = await asyncio.gather(
        call("POST", "/auth/refresh", credentials),
        call("PATCH", f"/admin/users/{target_id}", issuer, json={"password": NEW}))
    assert reset.status_code == 200
    assert refreshed.status_code in {200, 401}
    await rejected(credentials)
    if refreshed.status_code == 200:
        await rejected(dict(refreshed.cookies))


async def test_password_change_vs_refresh_keeps_only_replacement_family(world):
    credentials = await sign_in()
    refreshed, changed = await asyncio.gather(
        call("POST", "/auth/refresh", credentials),
        call("POST", "/auth/change-password", credentials,
             json={"current_password": PASSWORD, "new_password": NEW}))
    assert changed.status_code == 200
    assert refreshed.status_code in {200, 401}
    await rejected(credentials)
    if refreshed.status_code == 200:
        await rejected(dict(refreshed.cookies))
    assert (await call("GET", "/auth/me", dict(changed.cookies))).status_code == 200


async def test_new_independent_connection_observes_committed_revocation(world):
    _, _, schema = world
    credentials = await sign_in()
    other = create_async_engine(URL, hide_parameters=True,
        connect_args={"server_settings": {"search_path": schema}})
    maker = async_sessionmaker(other)
    try:
        async with maker() as db:
            user = await get_current_user(Request({"type": "http", "headers": []}),
                                          credentials["access_token"], db)
            assert user.username == "race-target"
        assert (await call("POST", "/auth/logout", credentials)).status_code == 200
        async with maker() as db:
            with pytest.raises(HTTPException) as error:
                await get_current_user(Request({"type": "http", "headers": []}),
                                       credentials["access_token"], db)
            assert error.value.status_code == 401
    finally:
        await other.dispose()


async def test_login_verified_before_reset_cannot_issue_session_after_reset(world, monkeypatch):
    _, target_id, _ = world
    issuer = await sign_in("race-issuer")
    verified, release = asyncio.Event(), asyncio.Event()
    original = routes.verify_password_async

    async def paused(password, encoded):
        result = await original(password, encoded)
        verified.set()
        await release.wait()
        return result

    monkeypatch.setattr(routes, "verify_password_async", paused)
    attempt = asyncio.create_task(call("POST", "/auth/admin-login", json={
        "identifier": "race-target", "password": PASSWORD}))
    try:
        await asyncio.wait_for(verified.wait(), 5)
        assert (await call("PATCH", f"/admin/users/{target_id}", issuer,
                           json={"password": NEW})).status_code == 200
        release.set()
        assert (await asyncio.wait_for(attempt, 5)).status_code == 401
    finally:
        release.set()
        await asyncio.gather(attempt, return_exceptions=True)
