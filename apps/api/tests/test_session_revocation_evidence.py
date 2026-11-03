"""Reproduce the documented stateless access-token revocation limitation.

HTTP200 assertions below are evidence of SEC-02's unresolved containment window. They are
not security acceptance criteria. If session-bound access revocation is implemented, replace
these evidence assertions with HTTP401 and update the audit finding.
"""
from tests.conftest import login


async def test_known_limitation_copied_access_works_after_logout(client):
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    copied_access = client.cookies.get("access_token")
    copied_refresh = client.cookies.get("refresh_token")
    assert (await client.post("/api/auth/logout")).status_code == 200
    client.cookies.clear()
    client.cookies.set("access_token", copied_access)
    # Refreshed sessions are revoked, but a copied short-lived access JWT still works.
    assert (await client.get("/api/portal/dashboard")).status_code == 200
    assert (await client.post(
        "/api/auth/refresh", headers={"Cookie": f"refresh_token={copied_refresh}"}
    )).status_code == 401


async def test_known_limitation_copied_access_works_after_reset_and_password_change(client, seeded):
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    copied_access = client.cookies.get("access_token")
    copied_refresh = client.cookies.get("refresh_token")
    client.cookies.clear()
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    assert (await client.post(
        f"/api/portal/alcs/{seeded['alc_a'].id}/reset-password",
        json={"password": "SyntheticTempReset1!"},
    )).status_code == 200
    client.cookies.clear()
    client.cookies.set("access_token", copied_access)
    assert (await client.get("/api/portal/dashboard")).status_code == 403

    client.cookies.clear()
    await login(client, "00010001", "SyntheticTempReset1!", "ALC")
    assert (await client.post(
        "/api/auth/change-password",
        json={"current_password": "SyntheticTempReset1!",
              "new_password": "SyntheticNewPrivate2!"},
    )).status_code == 200
    client.cookies.clear()
    client.cookies.set("access_token", copied_access)
    # Clearing forced-change makes the old still-unexpired access JWT usable again.
    assert (await client.get("/api/portal/dashboard")).status_code == 200
    assert (await client.post(
        "/api/auth/refresh", headers={"Cookie": f"refresh_token={copied_refresh}"}
    )).status_code == 401
