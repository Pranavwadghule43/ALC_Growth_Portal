"""Separate admin authentication: /api/auth/login serves SBU + ALC only, while
/api/auth/admin-login serves ADMIN only. Both reject the other roles at the backend,
return generic errors, and preserve refresh-token, is_active, and role authorization."""
import pytest

PORTAL_LOGIN = "/api/auth/login"
ADMIN_LOGIN = "/api/auth/admin-login"
PORTAL_ERROR = "Invalid username/ALC code or password"
ADMIN_ERROR = "Invalid username or password"


async def post_login(client, path, identifier, password):
    return await client.post(path, json={"identifier": identifier, "password": password})


# 1. ADMIN can log in through /api/auth/admin-login.
@pytest.mark.asyncio
async def test_admin_can_use_admin_login(client):
    resp = await post_login(client, ADMIN_LOGIN, "admin", "StrongAdminPass!")
    assert resp.status_code == 200 and resp.json()["role"] == "ADMIN"


# 2. ADMIN cannot log in through /api/auth/login (generic error, no existence disclosure).
@pytest.mark.asyncio
async def test_admin_rejected_on_portal_login(client):
    resp = await post_login(client, PORTAL_LOGIN, "admin", "StrongAdminPass!")
    assert resp.status_code == 401
    assert resp.json()["detail"] == PORTAL_ERROR


# 3. SBU can log in through /api/auth/login.
@pytest.mark.asyncio
async def test_sbu_can_use_portal_login(client):
    resp = await post_login(client, PORTAL_LOGIN, "sbu-4", "StrongSbuPass4!")
    assert resp.status_code == 200 and resp.json()["role"] == "SBU"


# 4. ALC can log in through /api/auth/login.
@pytest.mark.asyncio
async def test_alc_can_use_portal_login(client):
    resp = await post_login(client, PORTAL_LOGIN, "00010001", "StrongAlcPassA!")
    assert resp.status_code == 200 and resp.json()["role"] == "ALC"


# 5. SBU cannot log in through /api/auth/admin-login.
@pytest.mark.asyncio
async def test_sbu_rejected_on_admin_login(client):
    resp = await post_login(client, ADMIN_LOGIN, "sbu-4", "StrongSbuPass4!")
    assert resp.status_code == 401
    assert resp.json()["detail"] == ADMIN_ERROR


# 6. ALC cannot log in through /api/auth/admin-login.
@pytest.mark.asyncio
async def test_alc_rejected_on_admin_login(client):
    resp = await post_login(client, ADMIN_LOGIN, "00010001", "StrongAlcPassA!")
    assert resp.status_code == 401
    assert resp.json()["detail"] == ADMIN_ERROR


# 7. Inactive ADMIN cannot log in.
@pytest.mark.asyncio
async def test_inactive_admin_cannot_login(client, session, seeded):
    seeded["admin"].is_active = False
    await session.commit()
    resp = await post_login(client, ADMIN_LOGIN, "admin", "StrongAdminPass!")
    assert resp.status_code == 401
    assert resp.json()["detail"] == ADMIN_ERROR


# 8. Inactive SBU / ALC cannot log in.
@pytest.mark.asyncio
async def test_inactive_portal_users_cannot_login(client, session, seeded):
    seeded["sbu4_user"].is_active = False
    seeded["a"].is_active = False
    await session.commit()
    sbu = await post_login(client, PORTAL_LOGIN, "sbu-4", "StrongSbuPass4!")
    alc = await post_login(client, PORTAL_LOGIN, "00010001", "StrongAlcPassA!")
    assert sbu.status_code == 401 and sbu.json()["detail"] == PORTAL_ERROR
    assert alc.status_code == 401 and alc.json()["detail"] == PORTAL_ERROR


# 9. Existing refresh-token behavior still works after the split (both endpoints).
@pytest.mark.asyncio
async def test_refresh_after_admin_and_portal_login(client):
    assert (await post_login(client, ADMIN_LOGIN, "admin", "StrongAdminPass!")).status_code == 200
    admin_refresh = await client.post("/api/auth/refresh")
    assert admin_refresh.status_code == 200 and admin_refresh.json()["role"] == "ADMIN"
    client.cookies.clear()
    assert (await post_login(client, PORTAL_LOGIN, "sbu-4", "StrongSbuPass4!")).status_code == 200
    sbu_refresh = await client.post("/api/auth/refresh")
    assert sbu_refresh.status_code == 200 and sbu_refresh.json()["role"] == "SBU"


# 10. Role authorization still holds after authentication through the split endpoints.
@pytest.mark.asyncio
async def test_role_authorization_after_login(client):
    # ADMIN authenticated via admin-login reaches admin-only endpoints.
    assert (await post_login(client, ADMIN_LOGIN, "admin", "StrongAdminPass!")).status_code == 200
    csrf = client.cookies.get("csrf_token")
    client.headers["X-CSRF-Token"] = csrf
    assert (await client.get("/api/admin/dashboard")).status_code == 200
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    # SBU authenticated via portal login is still barred from admin endpoints.
    assert (await post_login(client, PORTAL_LOGIN, "sbu-4", "StrongSbuPass4!")).status_code == 200
    client.headers["X-CSRF-Token"] = client.cookies.get("csrf_token")
    assert (await client.get("/api/admin/dashboard")).status_code == 403
    assert (await client.get("/api/portal/dashboard")).status_code == 200
