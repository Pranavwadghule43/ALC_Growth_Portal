"""Reviewed evidence must survive an ALC correction.

Before: when an ALC removed evidence while an activity was CORRECTION_REQUIRED, the stored file
was deleted, so the evidence the reviewer had actually looked at was lost from the history.
Now: once an activity has been submitted, removing evidence only hides it; the file is kept.
Evidence on a never-submitted draft is still removed from storage.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.models import ActivityEvidence, AuditLog, User
from app.routes import portal as portal_routes
from tests.conftest import login

ACTIVITY = {
    "activity_type": "Partner meeting",
    "ecosystem": "College",
    "activity_date": str(date.today()),
    "location": "Pune",
    "learners_reached": 10,
    "leads_generated": 2,
    "admissions_generated": 0,
    "description": "Discussed a structured learner outreach pilot.",
    "outcome": "Pilot agreed",
}


def logout(client):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)


@pytest.fixture
def storage_deletes(monkeypatch):
    deleted: list[str] = []

    async def fake_delete(key):
        deleted.append(key)

    monkeypatch.setattr(portal_routes.storage_service, "delete", fake_delete)
    return deleted


async def draft_with_evidence(client, session):
    logout(client)
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    activity = (await client.post("/api/portal/activities", json=ACTIVITY)).json()
    user = await session.scalar(select(User).where(User.username == "alc-a"))
    evidence = ActivityEvidence(
        activity_id=uuid.UUID(activity["id"]),
        storage_key=f"private/{uuid.uuid4()}.pdf",
        original_filename="signed-mou.pdf",
        mime_type="application/pdf",
        file_size=10,
        uploaded_by=user.id,
    )
    session.add(evidence)
    await session.commit()
    return activity["id"], evidence


async def audit_for(session, evidence_id):
    return await session.scalar(
        select(AuditLog).where(
            AuditLog.action == "evidence_deleted", AuditLog.entity_id == str(evidence_id)
        )
    )


@pytest.mark.asyncio
async def test_draft_evidence_is_removed_from_storage(client, session, seeded, storage_deletes):
    activity_id, evidence = await draft_with_evidence(client, session)
    assert (await client.delete(f"/api/portal/evidence/{evidence.id}")).status_code == 200
    assert storage_deletes == [evidence.storage_key]
    detail = (await client.get(f"/api/portal/activities/{activity_id}")).json()
    assert detail["evidence"] == []
    assert (await audit_for(session, evidence.id)).audit_metadata["file_retained"] is False


@pytest.mark.asyncio
async def test_reviewed_evidence_is_kept_during_correction(
    client, session, seeded, storage_deletes
):
    activity_id, evidence = await draft_with_evidence(client, session)
    assert (await client.post(f"/api/portal/activities/{activity_id}/submit")).status_code == 200

    logout(client)
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    resp = await client.post(
        f"/api/portal/activities/{activity_id}/request-correction",
        json={"remark": "Replace the unsigned MoU"},
    )
    assert resp.status_code == 200

    logout(client)
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    assert (await client.delete(f"/api/portal/evidence/{evidence.id}")).status_code == 200

    # Hidden from the activity for everyone...
    detail = (await client.get(f"/api/portal/activities/{activity_id}")).json()
    assert detail["evidence"] == []
    # ...but the stored file was not deleted and the record still exists.
    assert storage_deletes == []
    await session.refresh(evidence)
    assert evidence.is_active is False
    assert (await audit_for(session, evidence.id)).audit_metadata["file_retained"] is True