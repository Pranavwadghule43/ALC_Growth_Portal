"""Duplicate-centre login and supervisor reset selection regressions."""

import pytest
from sqlalchemy import select

from app.auth import verify_password_async
from app.enums import Role
from app.models import User
from tests.conftest import login

TEMP = "TemporaryReset123!"


def logout(client):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)


@pytest.mark.asyncio
async def test_single_account_centre_code_and_unique_username_login(client, seeded):
    assert (await login(client, "00010001", "StrongAlcPassA!", "ALC")).status_code == 200
    logout(client)
    response = await login(client, "alc-a", "StrongAlcPassA!", "ALC")
    assert response.status_code == 200
    assert response.json()["id"] == str(seeded["a"].id)


@pytest.mark.asyncio
async def test_duplicate_centre_requires_unique_username_when_code_is_only_fallback(
    client, session, seeded, seed_password_hashes
):
    duplicate = User(
        username="alc-a-second",
        password_hash=seed_password_hashes["StrongAlcPassB!"],
        role=Role.ALC,
        alc_id=seeded["alc_a"].id,
    )
    session.add(duplicate)
    await session.commit()

    ambiguous = await login(client, "00010001", "StrongAlcPassA!", "ALC")
    assert ambiguous.status_code == 401
    assert ambiguous.json()["detail"] == "Invalid username/ALC code or password"

    for identifier, password, expected_id in (
        ("alc-a", "StrongAlcPassA!", seeded["a"].id),
        ("alc-a-second", "StrongAlcPassB!", duplicate.id),
    ):
        logout(client)
        response = await login(client, identifier, password, "ALC")
        assert response.status_code == 200
        assert response.json()["id"] == str(expected_id)


@pytest.mark.asyncio
async def test_username_equal_to_centre_code_has_priority_and_keeps_both_accounts_accessible(
    client, session, seeded, seed_password_hashes
):
    seeded["a"].username = seeded["alc_a"].alc_code
    duplicate = User(
        username="alc-00010001",
        password_hash=seed_password_hashes["StrongAlcPassB!"],
        role=Role.ALC,
        alc_id=seeded["alc_a"].id,
    )
    session.add(duplicate)
    await session.commit()

    first = await login(client, "00010001", "StrongAlcPassA!", "ALC")
    assert first.status_code == 200 and first.json()["id"] == str(seeded["a"].id)
    logout(client)
    second = await login(client, "alc-00010001", "StrongAlcPassB!", "ALC")
    assert second.status_code == 200 and second.json()["id"] == str(duplicate.id)


@pytest.mark.asyncio
async def test_inactive_wrong_password_admin_and_existing_sbu_behavior(client, session, seeded):
    seeded["a"].is_active = False
    seeded["sbu4_user"].email = "sbu4@example.com"
    await session.commit()

    for identifier, password in (
        ("alc-a", "StrongAlcPassA!"),
        ("00010001", "StrongAlcPassA!"),
        ("alc-b", "WrongPassword123!"),
        ("admin", "StrongAdminPass!"),
    ):
        logout(client)
        assert (await login(client, identifier, password, "ALC")).status_code == 401

    for identifier in ("sbu-4", "sbu4@example.com"):
        logout(client)
        assert (await login(client, identifier, "StrongSbuPass4!", "SBU")).status_code == 200

    logout(client)
    assert (await login(client, "admin", "StrongAdminPass!", "ADMIN")).status_code == 200


@pytest.mark.asyncio
async def test_supervisor_must_select_one_duplicate_account(
    client, session, seeded, seed_password_hashes
):
    duplicate = User(
        username="alc-a-second",
        password_hash=seed_password_hashes["StrongAlcPassB!"],
        role=Role.ALC,
        alc_id=seeded["alc_a"].id,
    )
    session.add(duplicate)
    await session.commit()
    original_first = seeded["a"].password_hash
    original_second = duplicate.password_hash

    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    path = f"/api/portal/alcs/{seeded['alc_a'].id}/reset-password"
    ambiguous = await client.post(path, json={"password": TEMP})
    assert ambiguous.status_code == 409
    await session.refresh(seeded["a"])
    await session.refresh(duplicate)
    assert seeded["a"].password_hash == original_first
    assert duplicate.password_hash == original_second

    reset = await client.post(path, json={"password": TEMP, "user_id": str(duplicate.id)})
    assert reset.status_code == 200
    assert reset.json()["user_id"] == str(duplicate.id)
    await session.refresh(seeded["a"])
    await session.refresh(duplicate)
    assert seeded["a"].password_hash == original_first
    assert duplicate.must_change_password is True
    assert await verify_password_async(TEMP, duplicate.password_hash)

    detail = await client.get(f"/api/portal/alcs/{seeded['alc_a'].id}")
    assert {item["id"] for item in detail.json()["alc_users"]} == {
        str(seeded["a"].id), str(duplicate.id)
    }


@pytest.mark.asyncio
async def test_reset_rejects_out_of_scope_and_non_alc_user_ids(client, session, seeded):
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    own_path = f"/api/portal/alcs/{seeded['alc_a'].id}/reset-password"

    non_alc = await client.post(
        own_path,
        json={"password": TEMP, "user_id": str(seeded["sbu4_user"].id)},
    )
    assert non_alc.status_code == 404

    outside = await client.post(
        f"/api/portal/alcs/{seeded['alc_b'].id}/reset-password",
        json={"password": TEMP, "user_id": str(seeded["b"].id)},
    )
    assert outside.status_code == 404

    # The failed attempts did not modify either account.
    rows = await session.execute(
        select(User.id, User.must_change_password).where(
            User.id.in_([seeded["sbu4_user"].id, seeded["b"].id])
        )
    )
    assert all(not must_change for _, must_change in rows)

