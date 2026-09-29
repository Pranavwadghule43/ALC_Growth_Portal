"""Phase 4G-A: login timing hardening.

Every login attempt performs exactly ONE Argon2 verification, whatever the outcome:

    unknown identifier           -> dummy hash (made once per process)  -> generic 401
    existing account, any state  -> the account's own hash              -> existing flow

so response time no longer reveals whether a username / ALC code / email exists.
These tests are structural (they count and inspect verification calls); they never
assert on wall-clock time.
"""
import asyncio
import threading
import weakref

import pytest
from sqlalchemy import func, select

from app import auth as auth_module
from app.auth import DUMMY_PASSWORD_HASH, hash_password, password_hasher, verify_password
from app.enums import Role
from app.models import DCU, RefreshToken, User
from app.routes import auth as auth_routes
from tests.conftest import login

ALC_A = ("00010001", "StrongAlcPassA!", "ALC")
SBU_4 = ("sbu-4", "StrongSbuPass4!", "SBU")
ADMIN = ("admin", "StrongAdminPass!", "ADMIN")
DCU_T = ("dcu-timing", "StrongDcuPass123!", "DCU")
OPERATIONAL_INVALID = "Invalid username/ALC code or password"
ADMIN_INVALID = "Invalid username or password"


def logout(client):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)


@pytest.fixture
def verify_calls(monkeypatch):
    """Record every async verification the login routes make (still running the real one)."""
    calls: list[str] = []
    real = auth_routes.verify_password_async

    async def spy(password, encoded):
        calls.append(encoded)
        return await real(password, encoded)

    monkeypatch.setattr(auth_routes, "verify_password_async", spy)
    return calls


@pytest.fixture
def argon2_calls(monkeypatch):
    """Record every real Argon2 verification and the thread it ran on."""
    calls: list[tuple[str, str]] = []
    real = auth_module.verify_password

    def spy(password, encoded):
        calls.append((encoded, threading.current_thread().name))
        return real(password, encoded)

    monkeypatch.setattr(auth_module, "verify_password", spy)
    return calls


@pytest.fixture
async def dcu_user(session, seeded):
    nashik = await session.scalar(select(DCU).where(DCU.code == "DCU_NASHIK"))
    user = User(
        username=DCU_T[0],
        password_hash=hash_password(DCU_T[1]),
        role=Role.DCU,
        dcu_id=nashik.id,
        must_change_password=False,
    )
    session.add(user)
    await session.commit()
    return user


async def attempt(client, who):
    logout(client)
    return await login(client, *who)


# --------------------------------------------------------------------------- #
# The dummy hash itself
# --------------------------------------------------------------------------- #
def test_dummy_hash_uses_the_real_argon2_configuration():
    params = f"$argon2id$v=19$m={password_hasher.memory_cost},t={password_hasher.time_cost}," \
             f"p={password_hasher.parallelism}$"
    assert DUMMY_PASSWORD_HASH.startswith(params)
    assert params == "$argon2id$v=19$m=65536,t=3,p=4$"  # unchanged Phase 4C parameters
    assert not password_hasher.check_needs_rehash(DUMMY_PASSWORD_HASH)
    assert verify_password("", DUMMY_PASSWORD_HASH) is False


@pytest.mark.asyncio
async def test_dummy_hash_is_not_generated_per_request(client, seeded, monkeypatch):
    def forbidden(password):
        raise AssertionError("login must not generate a hash")

    monkeypatch.setattr(auth_module, "hash_password", forbidden)
    before = auth_module.DUMMY_PASSWORD_HASH
    for _ in range(3):
        assert (await attempt(client, ("nobody-here", "whatever-pass-1", "SBU"))).status_code == 401
    assert auth_module.DUMMY_PASSWORD_HASH == before


# --------------------------------------------------------------------------- #
# Operational login: responses unchanged, one verification on every path
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "who",
    [("unknown-sbu", "SomePassword123!", "SBU"),   # unknown username
     ("99999999", "SomePassword123!", "ALC"),      # unknown ALC code
     ("ghost@example.com", "SomePassword123!", "SBU")],  # unknown email
)
async def test_unknown_operational_identifier_uses_dummy_hash_once(
    client, seeded, verify_calls, argon2_calls, who
):
    resp = await attempt(client, who)
    assert resp.status_code == 401 and resp.json()["detail"] == OPERATIONAL_INVALID
    assert verify_calls == [DUMMY_PASSWORD_HASH]
    assert [encoded for encoded, _ in argon2_calls] == [DUMMY_PASSWORD_HASH]
    assert "refresh_token" not in resp.cookies


@pytest.mark.asyncio
async def test_wrong_password_looks_exactly_like_unknown_user(
    client, seeded, verify_calls
):
    unknown = await attempt(client, ("unknown-sbu", "WrongPassword123!", "SBU"))
    wrong = await attempt(client, (SBU_4[0], "WrongPassword123!", "SBU"))
    assert (unknown.status_code, unknown.json()) == (wrong.status_code, wrong.json())
    assert wrong.json() == {"detail": OPERATIONAL_INVALID}
    assert verify_calls == [DUMMY_PASSWORD_HASH, seeded["sbu4_user"].password_hash]


@pytest.mark.asyncio
async def test_existing_operational_users_verify_their_own_hash_once(
    client, session, seeded, dcu_user, verify_calls
):
    seeded["sbu4_user"].email = "sbu4@example.com"
    await session.commit()
    cases = [
        (ALC_A, seeded["a"]),                                        # ALC code
        (SBU_4, seeded["sbu4_user"]),                                # username
        (("SBU4@example.com", SBU_4[1], "SBU"), seeded["sbu4_user"]),  # email, any case
        (DCU_T, dcu_user),
    ]
    for who, user in cases:
        verify_calls.clear()
        resp = await attempt(client, who)
        assert resp.status_code == 200, (who, resp.text)
        assert verify_calls == [user.password_hash]  # real hash, exactly once, no dummy
        verify_calls.clear()
        resp = await attempt(client, (who[0], "WrongPassword123!", who[2]))
        assert resp.status_code == 401 and resp.json()["detail"] == OPERATIONAL_INVALID
        assert verify_calls == [user.password_hash]


@pytest.mark.asyncio
async def test_inactive_user_is_unchanged_but_now_also_verifies_once(
    client, session, seeded, verify_calls
):
    seeded["sbu4_user"].is_active = False
    await session.commit()
    for password in (SBU_4[1], "WrongPassword123!"):  # correct or wrong: same generic 401
        verify_calls.clear()
        resp = await attempt(client, (SBU_4[0], password, "SBU"))
        assert resp.status_code == 401 and resp.json()["detail"] == OPERATIONAL_INVALID
        assert verify_calls == [seeded["sbu4_user"].password_hash]


@pytest.mark.asyncio
async def test_unavailable_hierarchy_only_revealed_after_correct_password(
    client, session, seeded, verify_calls
):
    seeded["sbu4"].is_active = False  # Centre A's SBU
    await session.commit()

    right = await attempt(client, ALC_A)
    assert right.status_code == 401 and right.json()["detail"] == "Account unavailable"
    assert "refresh_token" not in right.cookies

    wrong = await attempt(client, (ALC_A[0], "WrongPassword123!", "ALC"))
    assert wrong.status_code == 401 and wrong.json()["detail"] == OPERATIONAL_INVALID
    assert verify_calls == [seeded["a"].password_hash, seeded["a"].password_hash]


@pytest.mark.asyncio
async def test_admin_account_is_rejected_on_operational_login_with_dummy_work(
    client, seeded, verify_calls
):
    # Operational login never looks up ADMIN accounts: an admin identifier is "unknown" here.
    resp = await attempt(client, (ADMIN[0], ADMIN[1], "SBU"))
    assert resp.status_code == 401 and resp.json()["detail"] == OPERATIONAL_INVALID
    assert verify_calls == [DUMMY_PASSWORD_HASH]


# --------------------------------------------------------------------------- #
# Admin login
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_admin_login_paths(client, session, seeded, verify_calls):
    admin = await session.scalar(select(User).where(User.username == "admin"))

    unknown = await attempt(client, ("no-such-admin", "SomePassword123!", "ADMIN"))
    assert unknown.status_code == 401 and unknown.json()["detail"] == ADMIN_INVALID
    assert verify_calls == [DUMMY_PASSWORD_HASH]

    verify_calls.clear()
    wrong = await attempt(client, (ADMIN[0], "WrongPassword123!", "ADMIN"))
    assert (wrong.status_code, wrong.json()) == (unknown.status_code, unknown.json())
    assert verify_calls == [admin.password_hash]

    verify_calls.clear()
    operational_user = await attempt(client, (SBU_4[0], SBU_4[1], "ADMIN"))  # not an admin
    assert operational_user.json()["detail"] == ADMIN_INVALID
    assert verify_calls == [DUMMY_PASSWORD_HASH]

    verify_calls.clear()
    ok = await attempt(client, ADMIN)
    assert ok.status_code == 200 and ok.json()["role"] == "ADMIN"
    assert verify_calls == [admin.password_hash]


# --------------------------------------------------------------------------- #
# Async offload + the Phase 4C concurrency limiter stay in the path
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_verification_runs_in_a_worker_thread(client, seeded, argon2_calls):
    await attempt(client, ("unknown-sbu", "SomePassword123!", "SBU"))
    await attempt(client, ALC_A)
    main = threading.main_thread().name
    assert len(argon2_calls) == 2
    assert all(thread != main for _, thread in argon2_calls)


@pytest.mark.asyncio
async def test_dummy_verification_waits_for_the_shared_limiter(
    client, seeded, monkeypatch, argon2_calls
):
    monkeypatch.setattr(auth_module.settings, "password_hash_concurrency", 1)
    monkeypatch.setattr(auth_module, "_limiters", weakref.WeakKeyDictionary())
    limiter = auth_module.password_limiter()
    await limiter.acquire()  # occupy the only Argon2 slot
    logout(client)
    pending = asyncio.create_task(
        client.post("/api/auth/login", json={"identifier": "unknown", "password": "x" * 12})
    )
    await asyncio.sleep(0.2)
    assert not pending.done() and argon2_calls == []  # queued behind the limiter
    limiter.release()
    resp = await pending
    assert resp.status_code == 401
    assert [encoded for encoded, _ in argon2_calls] == [DUMMY_PASSWORD_HASH]
    assert limiter.borrowed_tokens == 0


# --------------------------------------------------------------------------- #
# Regression: successful login and forced password change unchanged
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_successful_login_creates_the_usual_session(client, session, seeded):
    before = await session.scalar(select(func.count(RefreshToken.id)))
    resp = await attempt(client, ALC_A)
    assert resp.status_code == 200 and resp.json()["role"] == "ALC"
    assert {"access_token", "refresh_token", "csrf_token"} <= set(resp.cookies.keys())
    assert await session.scalar(select(func.count(RefreshToken.id))) == before + 1
    assert (await client.get("/api/auth/me")).json()["role"] == "ALC"
    assert (await client.post("/api/auth/refresh")).status_code == 200


@pytest.mark.asyncio
async def test_must_change_password_unchanged(client, session, seeded):
    seeded["a"].must_change_password = True
    await session.commit()
    resp = await attempt(client, ALC_A)
    assert resp.status_code == 200 and resp.json()["must_change_password"] is True
    blocked = await client.get("/api/portal/dashboard")
    assert blocked.status_code == 403 and blocked.json()["detail"] == "Password change required"