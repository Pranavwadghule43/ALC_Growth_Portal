"""Admin deletion of ALC/SBU login accounts: preserves master + historical data, revokes
sessions, enforces ADMIN/self protection and role authorization, and writes an audit event."""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.enums import ActivityStatus, Role
from app.models import ALC, SBU, Activity, ActivityEvidence, AuditLog, RefreshToken, User
from tests.conftest import login


async def seed_history(session, seeded):
    """Give ALC 'a' an activity (created_by), evidence (uploaded_by) and a refresh token."""
    activity = Activity(
        alc_id=seeded["alc_a"].id,
        activity_number="ACT-DEL-1",
        activity_type="Partner meeting",
        ecosystem="College",
        activity_date=date.today(),
        location="Pune",
        description="desc",
        outcome="outcome",
        created_by=seeded["a"].id,
        status=ActivityStatus.SUBMITTED,
    )
    session.add(activity)
    await session.flush()
    session.add(
        ActivityEvidence(
            activity_id=activity.id,
            storage_key=f"private/{uuid.uuid4()}.pdf",
            original_filename="evidence.pdf",
            mime_type="application/pdf",
            file_size=5,
            uploaded_by=seeded["a"].id,
        )
    )
    session.add(
        RefreshToken(
            user_id=seeded["a"].id,
            token_hash=f"hash-{uuid.uuid4().hex}",
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        )
    )
    await session.commit()
    return activity


# 1–5, 9, 10, 15: delete an ALC login and verify preservation + cleanup + audit
@pytest.mark.asyncio
async def test_delete_alc_login_preserves_everything(client, session, seeded):
    activity = await seed_history(session, seeded)
    alc_id, user_id = seeded["alc_a"].id, seeded["a"].id

    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    resp = await client.request("DELETE", f"/api/admin/users/{user_id}")
    assert resp.status_code == 200  # (1) deleted successfully

    # (login gone)
    assert await session.get(User, user_id) is None
    # (2) ALC master record remains, unchanged
    alc = await session.get(ALC, alc_id)
    assert alc is not None and alc.alc_code == "00010001" and alc.alc_name == "Centre A"
    assert alc.sbu_id == seeded["sbu4"].id
    # (3) activity remains (authorship detached, not deleted)
    kept = await session.get(Activity, activity.id)
    assert kept is not None and kept.alc_id == alc_id and kept.created_by is None
    # (4) evidence remains
    assert await session.scalar(select(func.count(ActivityEvidence.id))) == 1
    # (9) refresh tokens revoked
    assert (
        await session.scalar(
            select(func.count(RefreshToken.id)).where(RefreshToken.user_id == user_id)
        )
        == 0
    )
    # (15) audit event recorded with the required fields, no secrets
    audit = await session.scalar(
        select(AuditLog).where(AuditLog.action == "USER_ACCOUNT_DELETED")
    )
    assert audit is not None
    assert audit.actor_user_id == seeded["admin"].id
    assert audit.entity_id == str(user_id)
    assert audit.audit_metadata["deleted_role"] == "ALC"
    assert audit.audit_metadata["alc_id"] == str(alc_id)

    # (10) the deleted user can no longer log in
    assert (await login(client, "00010001", "StrongAlcPassA!", "ALC")).status_code == 401

    # (5) admin can create a new login for the same ALC afterward
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    recreated = await client.post(
        "/api/admin/users",
        json={
            "username": "alc-a-new",
            "role": "ALC",
            "alc_id": str(alc_id),
            "password": "BrandNewAlcPass1!",
        },
    )
    assert recreated.status_code == 201 and recreated.json()["alc_id"] == str(alc_id)


# 6–8: delete an SBU login and verify master + assignments remain
@pytest.mark.asyncio
async def test_delete_sbu_login_preserves_sbu_and_assignments(client, session, seeded):
    sbu_id, user_id = seeded["sbu4"].id, seeded["sbu4_user"].id
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    resp = await client.request("DELETE", f"/api/admin/users/{user_id}")
    assert resp.status_code == 200  # (6)
    assert await session.get(User, user_id) is None
    # (7) SBU master remains
    assert await session.get(SBU, sbu_id) is not None
    # (8) SBU -> ALC assignments remain
    assigned = await session.scalar(
        select(func.count(ALC.id)).where(ALC.sbu_id == sbu_id)
    )
    assert assigned == 2  # Centre A and Centre C


# 11: ADMIN accounts cannot be deleted (backend rejects even a direct call)
@pytest.mark.asyncio
async def test_admin_account_cannot_be_deleted(client, session, seeded):
    other_admin = User(
        username="admin2", password_hash="x", role=Role.ADMIN, is_active=True
    )
    session.add(other_admin)
    await session.commit()
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    resp = await client.request("DELETE", f"/api/admin/users/{other_admin.id}")
    assert resp.status_code == 403
    assert await session.get(User, other_admin.id) is not None


# 12: the logged-in admin cannot delete their own account
@pytest.mark.asyncio
async def test_admin_cannot_delete_self(client, seeded):
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    resp = await client.request("DELETE", f"/api/admin/users/{seeded['admin'].id}")
    assert resp.status_code == 403


# 13, 14: non-admins cannot call the delete endpoint
@pytest.mark.asyncio
async def test_sbu_cannot_delete(client, seeded):
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    assert (
        await client.request("DELETE", f"/api/admin/users/{seeded['b'].id}")
    ).status_code == 403


@pytest.mark.asyncio
async def test_alc_cannot_delete(client, seeded):
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    assert (
        await client.request("DELETE", f"/api/admin/users/{seeded['b'].id}")
    ).status_code == 403


# not found
@pytest.mark.asyncio
async def test_delete_missing_user_404(client, seeded):
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    assert (
        await client.request("DELETE", f"/api/admin/users/{uuid.uuid4()}")
    ).status_code == 404
