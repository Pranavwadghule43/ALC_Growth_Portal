"""Regression tests for password reset / change session security.

Bug 1: after an SBU/admin reset, the user could "change" the temporary password to itself,
       clearing must_change_password while the resetter still knew the password.
Bug 2: a reset or change did not end sessions opened earlier, so an old browser kept access.
"""
import pytest

from tests.conftest import login
from tests.test_dcu_hierarchy import NASHIK_ALC_SBU4, hier  # noqa: F401  (hierarchy fixture)
from tests.test_dcu_hierarchy import PW as HIER_PW

TEMP = "TempResetPass123!"
NEW = "MyOwnNewAlcPass!"


def logout(client):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)


def snapshot(client):
    return {k: client.cookies.get(k) for k in ("access_token", "refresh_token", "csrf_token")}


def restore(client, saved):
    logout(client)
    for name, value in saved.items():
        client.cookies.set(name, value)
    client.headers["X-CSRF-Token"] = saved["csrf_token"]


async def sbu_resets_alc_a(client, seeded):
    logout(client)
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    r = await client.post(
        f"/api/portal/alcs/{seeded['alc_a'].id}/reset-password", json={"password": TEMP}
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_cannot_change_to_same_temporary_password(client, seeded):
    await sbu_resets_alc_a(client, seeded)
    logout(client)
    await login(client, "00010001", TEMP, "ALC")
    r = await client.post(
        "/api/auth/change-password", json={"current_password": TEMP, "new_password": TEMP}
    )
    assert r.status_code == 400
    assert "different" in r.json()["detail"]
    # flag is still set, so the portal stays locked until a real change
    assert (await client.get("/api/auth/me")).json()["must_change_password"] is True
    assert (await client.get("/api/portal/dashboard")).status_code == 403


@pytest.mark.asyncio
async def test_sbu_reset_ends_existing_alc_sessions(client, seeded):
    logout(client)
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    old_browser = snapshot(client)

    await sbu_resets_alc_a(client, seeded)

    restore(client, old_browser)
    assert (await client.post("/api/auth/refresh")).status_code == 401


@pytest.mark.asyncio
async def test_admin_reset_ends_existing_sessions(client, seeded):
    logout(client)
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    old_browser = snapshot(client)

    logout(client)
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    r = await client.patch(
        f"/api/admin/users/{seeded['sbu4_user'].id}", json={"password": "AdminTempPass123!"}
    )
    assert r.status_code == 200

    restore(client, old_browser)
    assert (await client.post("/api/auth/refresh")).status_code == 401


@pytest.mark.asyncio
async def test_change_password_keeps_current_session_but_ends_others(client, seeded):
    await sbu_resets_alc_a(client, seeded)
    logout(client)
    await login(client, "00010001", TEMP, "ALC")
    other_browser = snapshot(client)

    logout(client)
    await login(client, "00010001", TEMP, "ALC")
    r = await client.post(
        "/api/auth/change-password", json={"current_password": TEMP, "new_password": NEW}
    )
    assert r.status_code == 200
    # the browser that made the change stays signed in
    assert (await client.post("/api/auth/refresh")).status_code == 200
    assert (await client.get("/api/portal/dashboard")).status_code == 200

    # the other browser is signed out
    restore(client, other_browser)
    assert (await client.post("/api/auth/refresh")).status_code == 401

    # only the new password works now
    logout(client)
    assert (await login(client, "00010001", TEMP, "ALC")).status_code == 401
    assert (await login(client, "00010001", NEW, "ALC")).status_code == 200


# --- DCU supervisors (Phase 1/2 hierarchy) --------------------------------- #
@pytest.mark.asyncio
async def test_dcu_reset_ends_alc_sessions_and_is_scoped(client, hier):  # noqa: F811
    logout(client)
    await login(client, NASHIK_ALC_SBU4, HIER_PW, "ALC")
    old_browser = snapshot(client)

    # Another DCU cannot reset a Nashik ALC
    logout(client)
    await login(client, "dcu-pn", HIER_PW, "DCU")
    r = await client.post(
        f"/api/portal/alcs/{hier['alc4_centre'].id}/reset-password", json={"password": TEMP}
    )
    assert r.status_code == 404

    # DCU Nashik can, and the ALC's existing session is ended
    logout(client)
    await login(client, "dcu-nashik", HIER_PW, "DCU")
    r = await client.post(
        f"/api/portal/alcs/{hier['alc4_centre'].id}/reset-password", json={"password": TEMP}
    )
    assert r.status_code == 200

    restore(client, old_browser)
    assert (await client.post("/api/auth/refresh")).status_code == 401