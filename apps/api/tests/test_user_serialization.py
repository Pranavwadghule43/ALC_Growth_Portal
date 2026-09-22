"""Regression tests for the ``MissingGreenlet`` response-serialization bug.

``UserOut`` serializes the nested chain ``user -> alc -> sbu``. If an endpoint
returns a User ORM object without eagerly loading ``User.alc.sbu``, Pydantic
triggers an async lazy load while building the response and the request fails
with ``ResponseValidationError`` / ``MissingGreenlet`` (a 500). SBU logins are
unaffected because an SBU user has no ``alc``; ALC logins hit it.

The shared test session keeps related objects in its identity map, which hides
the bug (a many-to-one resolves without SQL). ``expunge_all()`` before each
request clears that map so the endpoint's own eager loading is what determines
whether serialization has to lazy-load — i.e. it reproduces the fresh
per-request session used in production.
"""
import pytest
from sqlalchemy import select

from app.models import User
from tests.conftest import login


async def fresh(session):
    """Detach everything so the next request cannot resolve relationships from the
    identity map — forcing the endpoint to eager-load exactly what it returns."""
    session.expunge_all()


# 1-3. ALC login serializes the nested alc.sbu chain ------------------------ #
@pytest.mark.asyncio
async def test_alc_login_returns_nested_alc_sbu(client, session):
    await fresh(session)
    resp = await login(client, "00010001", "StrongAlcPassA!", "ALC")
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "ALC"
    assert body["alc"]["alc_code"] == "00010001"
    # The nested SBU must serialize (Centre A belongs to SBU 4).
    assert body["alc"]["sbu"] is not None
    assert body["alc"]["sbu"]["code"] == "SBU 4"


# 4. SBU login still works -------------------------------------------------- #
@pytest.mark.asyncio
async def test_sbu_login_still_works(client, session):
    await fresh(session)
    resp = await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    assert resp.status_code == 200
    assert resp.json()["role"] == "SBU"
    assert resp.json()["sbu"]["code"] == "SBU 4"
    assert resp.json()["alc"] is None


# 8. /api/auth/me for an ALC works ------------------------------------------ #
@pytest.mark.asyncio
async def test_alc_me_returns_nested_alc_sbu(client, session):
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    await fresh(session)
    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["alc"]["sbu"]["code"] == "SBU 4"


# 5-7. Admin create / update an ALC user serialize correctly ---------------- #
@pytest.mark.asyncio
async def test_admin_create_alc_user_serializes(client, session, seeded):
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    await fresh(session)
    created = await client.post(
        "/api/admin/users",
        json={
            "username": "alc-a-second",
            "role": "ALC",
            "alc_id": str(seeded["alc_a"].id),
            "password": "StrongAlcPassNew1!",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["role"] == "ALC"
    assert body["alc"]["alc_code"] == "00010001"
    assert body["alc"]["sbu"]["code"] == "SBU 4"  # nested chain serialized

    # 6. The user actually exists in the database.
    row = await session.scalar(select(User).where(User.username == "alc-a-second"))
    assert row is not None and str(row.alc_id) == str(seeded["alc_a"].id)


@pytest.mark.asyncio
async def test_admin_update_alc_user_serializes(client, session, seeded):
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    target = await session.scalar(select(User).where(User.username == "alc-a"))
    await fresh(session)
    updated = await client.patch(
        f"/api/admin/users/{target.id}", json={"is_active": False}
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["is_active"] is False
    assert body["alc"]["sbu"]["code"] == "SBU 4"  # nested chain serialized


# 9. Existing admin/SBU behavior unchanged (users list serializes) ---------- #
@pytest.mark.asyncio
async def test_admin_users_list_serializes_all_roles(client, session):
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    await fresh(session)
    resp = await client.get("/api/admin/users")
    assert resp.status_code == 200
    users = resp.json()
    by_name = {u["username"]: u for u in users}
    # ALC user carries its nested SBU; SBU user carries its SBU; admin carries neither.
    assert by_name["alc-a"]["alc"]["sbu"]["code"] == "SBU 4"
    assert by_name["sbu-4"]["sbu"]["code"] == "SBU 4"
    assert by_name["admin"]["alc"] is None and by_name["admin"]["sbu"] is None
