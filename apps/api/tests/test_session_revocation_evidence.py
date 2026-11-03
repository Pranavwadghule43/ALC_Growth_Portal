"""P1-A acceptance tests replacing the original copied-JWT weakness reproductions."""
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from sqlalchemy import func, select

from app.auth import decode_access_token, new_refresh_token
from app.config import settings
from app.dependencies import AUTH_USER_LOADERS
from app.enums import Role
from app.models import AuthSession, RefreshToken, User
from app.services.sessions import cleanup_expired_sessions
from tests.conftest import login
from tests.test_activity_exports import StatementLog

PASSWORD = "StrongAlcPassA!"
NEW = "SyntheticNewPrivate2!"
TEMP = "SyntheticTempReset1!"


def snapshot(client):
    return {key: client.cookies.get(key) for key in ("access_token", "refresh_token", "csrf_token")}


def restore(client, cookies):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    for key, value in cookies.items():
        if value:
            client.cookies.set(key, value)
    if cookies.get("csrf_token"):
        client.headers["X-CSRF-Token"] = cookies["csrf_token"]


async def sign_in(client):
    restore(client, {})
    assert (await login(client, "00010001", PASSWORD, "ALC")).status_code == 200
    return snapshot(client)


async def assert_revoked(client, credentials):
    restore(client, credentials)
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/portal/dashboard")).status_code == 401
    assert (await client.post("/api/auth/refresh")).status_code == 401


async def test_copied_access_and_refresh_rejected_after_logout(client):
    original = await sign_in(client)
    assert (await client.post("/api/auth/logout")).status_code == 200
    assert not any(client.cookies.get(key) for key in original)
    await assert_revoked(client, original)


@pytest.mark.parametrize("resetter", ["SBU", "DCU", "ADMIN"])
async def test_reset_rejects_old_access_immediately_and_after_forced_change(
    client, session, seeded, resetter
):
    original = await sign_in(client)
    second = await sign_in(client)
    restore(client, {})
    if resetter == "ADMIN":
        await login(client, "admin", "StrongAdminPass!", "ADMIN")
        response = await client.patch(f"/api/admin/users/{seeded['a'].id}",
                                      json={"password": TEMP})
    else:
        name = "sbu-4"
        if resetter == "DCU":
            name = "revocation-dcu"
            session.add(User(username=name, password_hash=seeded["sbu4_user"].password_hash,
                             role=Role.DCU, dcu_id=seeded["sbu4"].dcu_id))
            await session.commit()
        await login(client, name, "StrongSbuPass4!", resetter)
        response = await client.post(
            f"/api/portal/alcs/{seeded['alc_a'].id}/reset-password", json={"password": TEMP})
    assert response.status_code == 200
    await assert_revoked(client, original)
    await assert_revoked(client, second)
    restore(client, {})
    assert (await login(client, "00010001", TEMP, "ALC")).json()["must_change_password"] is True
    assert (await client.get("/api/portal/dashboard")).status_code == 403
    assert (await client.post("/api/auth/change-password", json={
        "current_password": TEMP, "new_password": NEW})).status_code == 200
    assert (await client.get("/api/portal/dashboard")).status_code == 200
    await assert_revoked(client, original)
    await assert_revoked(client, second)


async def test_logout_one_device_preserves_other_device(client):
    first = await sign_in(client)
    second = await sign_in(client)
    assert decode_access_token(first["access_token"]).session_id != decode_access_token(
        second["access_token"]).session_id
    restore(client, first)
    assert (await client.post("/api/auth/logout")).status_code == 200
    await assert_revoked(client, first)
    restore(client, second)
    assert (await client.get("/api/portal/dashboard")).status_code == 200
    assert (await client.post("/api/auth/refresh")).status_code == 200


async def test_password_change_revokes_old_devices_and_issues_fresh_current_credentials(client):
    first = await sign_in(client)
    second = await sign_in(client)
    assert (await client.post("/api/auth/change-password", json={
        "current_password": PASSWORD, "new_password": NEW})).status_code == 200
    replacement = snapshot(client)
    assert replacement["access_token"] != second["access_token"]
    assert replacement["refresh_token"] != second["refresh_token"]
    assert (await client.get("/api/auth/me")).status_code == 200
    for old in (first, second):
        await assert_revoked(client, old)
    restore(client, replacement)
    assert (await client.get("/api/portal/dashboard")).status_code == 200
    assert (await client.post("/api/auth/refresh")).status_code == 200


@pytest.mark.parametrize("access_only", [True, False])
async def test_logout_can_use_access_only_or_stale_consumed_refresh_family(client, access_only):
    old = await sign_in(client)
    assert (await client.post("/api/auth/refresh")).status_code == 200
    successor = snapshot(client)
    restore(client, {**old, "refresh_token": None} if access_only
            else {**old, "access_token": None})
    assert (await client.post("/api/auth/logout")).status_code == 200
    await assert_revoked(client, old)
    await assert_revoked(client, successor)


async def test_refresh_replay_revokes_successor_access_and_refresh_only_in_that_family(client):
    other = await sign_in(client)
    original = await sign_in(client)
    assert (await client.post("/api/auth/refresh")).status_code == 200
    successor = snapshot(client)
    assert decode_access_token(original["access_token"]).session_id == decode_access_token(
        successor["access_token"]).session_id
    restore(client, original)
    assert (await client.post("/api/auth/refresh")).status_code == 401
    await assert_revoked(client, successor)
    restore(client, other)
    assert (await client.get("/api/auth/me")).status_code == 200


@pytest.mark.parametrize("missing", ["sid", "sub", "type", "iat", "exp"])
async def test_missing_required_claim_fails_closed(client, missing):
    original = await sign_in(client)
    payload = jwt.decode(original["access_token"], settings.secret_key, algorithms=["HS256"])
    payload.pop(missing)
    client.cookies.clear()
    client.cookies.set("access_token", jwt.encode(payload, settings.secret_key, algorithm="HS256"))
    assert (await client.get("/api/auth/me")).status_code == 401


@pytest.mark.parametrize("bad_sid", [None, 123, {}, "not-a-uuid", str(uuid.uuid4())])
async def test_malformed_or_nonexistent_session_claim_fails_closed(client, bad_sid):
    original = await sign_in(client)
    payload = jwt.decode(original["access_token"], settings.secret_key, algorithms=["HS256"])
    payload["sid"] = bad_sid
    client.cookies.clear()
    client.cookies.set("access_token", jwt.encode(payload, settings.secret_key, algorithm="HS256"))
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_subject_must_own_session_and_role_claim_cannot_elevate(client, seeded):
    original = await sign_in(client)
    payload = jwt.decode(original["access_token"], settings.secret_key, algorithms=["HS256"])
    payload["sub"] = str(seeded["admin"].id)
    client.cookies.clear()
    client.cookies.set("access_token", jwt.encode(payload, settings.secret_key, algorithm="HS256"))
    assert (await client.get("/api/auth/me")).status_code == 401
    payload["sub"], payload["role"] = str(seeded["a"].id), "ADMIN"
    client.cookies.set("access_token", jwt.encode(payload, settings.secret_key, algorithm="HS256"))
    assert (await client.get("/api/admin/dashboard")).status_code == 403


@pytest.mark.parametrize("invalid", ["expiry", "type", "algorithm", "signature", "issued_at"])
async def test_signature_algorithm_type_and_time_checks_remain_enforced(client, invalid):
    original = await sign_in(client)
    payload = jwt.decode(original["access_token"], settings.secret_key, algorithms=["HS256"])
    if invalid == "expiry":
        payload["exp"] = int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp())
    elif invalid == "issued_at":
        payload["iat"] = int((datetime.now(timezone.utc) + timedelta(minutes=1)).timestamp())
    elif invalid == "type":
        payload["type"] = "refresh"
    encoded = jwt.encode(payload,
        "different-synthetic-key-for-signature-test-only" if invalid == "signature"
        else settings.secret_key, algorithm="HS512" if invalid == "algorithm" else "HS256")
    client.cookies.clear()
    client.cookies.set("access_token", encoded)
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_expired_persistent_family_rejects_unexpired_access(client, session):
    original = await sign_in(client)
    identity = decode_access_token(original["access_token"])
    family = await session.get(AuthSession, identity.session_id)
    family.created_at = datetime.now(timezone.utc) - timedelta(days=2)
    family.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await session.commit()
    await assert_revoked(client, original)


async def test_legacy_refresh_row_is_preserved_but_cannot_authenticate(client, session, seeded):
    raw, digest = new_refresh_token()
    session.add(RefreshToken(user_id=seeded["a"].id, token_hash=digest,
                             expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
    await session.commit()
    assert (await client.post("/api/auth/refresh", headers={
        "Cookie": f"refresh_token={raw}"})).status_code == 401
    assert await session.scalar(select(RefreshToken.id).where(RefreshToken.token_hash == digest))


async def test_auth_session_join_adds_no_database_round_trips(client, session, seeded):
    await sign_in(client)
    with StatementLog(session) as before:
        await session.scalar(select(User).where(User.id == seeded["a"].id)
                             .options(*AUTH_USER_LOADERS).execution_options(populate_existing=True))
    with StatementLog(session) as after:
        assert (await client.get("/api/auth/me")).status_code == 200
    assert len(before.statements) == len(after.statements)
    assert len(after.touching("auth_sessions")) == 1


async def test_cleanup_is_expiry_bounded_and_keeps_live_families(client, session, seeded):
    original = await sign_in(client)
    for _ in range(3):
        family = AuthSession(user_id=seeded["a"].id,
                             created_at=datetime.now(timezone.utc) - timedelta(days=3),
                             expires_at=datetime.now(timezone.utc) - timedelta(days=1))
        session.add(family)
        await session.flush()
        for _ in range(2):
            _, digest = new_refresh_token()
            session.add(RefreshToken(user_id=seeded["a"].id, session_id=family.id,
                                      token_hash=digest, expires_at=family.expires_at))
    await session.commit()
    result = await cleanup_expired_sessions(session, batch_size=2)
    await session.commit()
    assert result["sessions_deleted"] == 2 and result["refresh_rows_deleted"] == 4
    assert await session.scalar(select(func.count(AuthSession.id))) == 2
    await cleanup_expired_sessions(session, batch_size=2)
    await session.commit()
    assert await session.scalar(select(func.count(AuthSession.id))) == 1
    restore(client, original)
    assert (await client.get("/api/auth/me")).status_code == 200
