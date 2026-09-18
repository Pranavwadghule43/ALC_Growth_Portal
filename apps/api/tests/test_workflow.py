import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.models import ActivityEvidence
from app.routes import alc as alc_routes
from tests.conftest import login

payload = {
    "activity_type": "Partner meeting",
    "ecosystem": "College",
    "collaboration_type": "Pilot discussion",
    "activity_date": str(date.today()),
    "location": "Pune",
    "learners_reached": 25,
    "leads_generated": 10,
    "admissions_generated": 2,
    "description": "Discussed a structured learner outreach pilot.",
    "outcome": "Pilot agreed",
}


@pytest.mark.asyncio
async def test_login_and_wrong_password(client):
    assert (await login(client, "00010001", "wrong-password", "ALC")).status_code == 401
    ok = await login(client, "00010001", "StrongAlcPassA!", "ALC")
    assert ok.status_code == 200 and ok.json()["alc"]["alc_name"] == "Centre A"


@pytest.mark.asyncio
async def test_inactive_and_mandatory_password_change(client, session, seeded):
    seeded["b"].is_active = False
    seeded["a"].must_change_password = True
    await session.commit()
    assert (await login(client, "00010002", "StrongAlcPassB!", "ALC")).status_code == 401
    assert (await login(client, "00010001", "StrongAlcPassA!", "ALC")).status_code == 200
    assert (await client.get("/api/alc/dashboard")).status_code == 403
    changed = await client.post(
        "/api/auth/change-password",
        json={"current_password": "StrongAlcPassA!", "new_password": "EvenStrongerAlcPassword!"},
    )
    assert changed.status_code == 200
    assert (await client.get("/api/alc/dashboard")).status_code == 200


@pytest.mark.asyncio
async def test_alc_ownership_isolation(client, session):
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    created = await client.post("/api/alc/activities", json=payload)
    assert created.status_code == 201
    activity_id = created.json()["id"]
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    await login(client, "00010002", "StrongAlcPassB!", "ALC")
    assert (await client.get(f"/api/alc/activities/{activity_id}")).status_code == 404
    assert (
        await client.patch(f"/api/alc/activities/{activity_id}", json=payload)
    ).status_code == 404
    from app.models import User

    evidence = ActivityEvidence(
        activity_id=uuid.UUID(created.json()["id"]),
        storage_key="private/test.pdf",
        original_filename="test.pdf",
        mime_type="application/pdf",
        file_size=5,
        uploaded_by=(await session.scalar(select(User).where(User.username == "alc-a"))).id,
    )
    session.add(evidence)
    await session.commit()
    assert (await client.get(f"/api/alc/evidence/{evidence.id}/access")).status_code == 404


@pytest.mark.asyncio
async def test_submission_correction_resubmission_verification(client, session):
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    activity = (await client.post("/api/alc/activities", json=payload)).json()
    from app.models import User

    user = await session.scalar(select(User).where(User.username == "alc-a"))
    session.add(
        ActivityEvidence(
            activity_id=uuid.UUID(activity["id"]),
            storage_key="private/workflow.pdf",
            original_filename="evidence.pdf",
            mime_type="application/pdf",
            file_size=5,
            uploaded_by=user.id,
        )
    )
    await session.commit()
    submitted = await client.post(f"/api/alc/activities/{activity['id']}/submit")
    assert submitted.status_code == 200 and submitted.json()["status"] == "SUBMITTED"
    assert (
        await client.patch(f"/api/alc/activities/{activity['id']}", json=payload)
    ).status_code == 409
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    corrected = await client.post(
        f"/api/admin/activities/{activity['id']}/request-correction",
        json={"remark": "Clarify the signed outcome"},
    )
    assert corrected.status_code == 200 and corrected.json()["status"] == "CORRECTION_REQUIRED"
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    assert (
        await client.patch(
            f"/api/alc/activities/{activity['id']}",
            json={**payload, "outcome": "Signed pilot confirmed"},
        )
    ).status_code == 200
    resubmitted = await client.post(f"/api/alc/activities/{activity['id']}/submit")
    assert resubmitted.json()["status"] == "RESUBMITTED"
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    verified = await client.post(
        f"/api/admin/activities/{activity['id']}/verify", json={"remark": "Evidence accepted"}
    )
    assert verified.status_code == 200 and verified.json()["status"] == "VERIFIED"


@pytest.mark.asyncio
async def test_reject_requires_reason(client, session):
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    activity = (await client.post("/api/alc/activities", json=payload)).json()
    from app.models import User

    user = await session.scalar(select(User).where(User.username == "alc-a"))
    session.add(
        ActivityEvidence(
            activity_id=uuid.UUID(activity["id"]),
            storage_key="private/reject.pdf",
            original_filename="evidence.pdf",
            mime_type="application/pdf",
            file_size=5,
            uploaded_by=user.id,
        )
    )
    await session.commit()
    await client.post(f"/api/alc/activities/{activity['id']}/submit")
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    assert (
        await client.post(f"/api/admin/activities/{activity['id']}/reject", json={})
    ).status_code == 422


@pytest.mark.asyncio
async def test_upload_validation_and_verified_metrics(client, monkeypatch):
    stored = {}

    async def upload(key, body, content_type):
        stored[key] = (body, content_type)

    async def secure_url(key):
        return f"https://private.example/{key}"

    monkeypatch.setattr(alc_routes.storage_service, "upload", upload)
    monkeypatch.setattr(alc_routes.storage_service, "get_secure_url", secure_url)
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    activity = (await client.post("/api/alc/activities", json=payload)).json()
    bad = await client.post(
        f"/api/alc/activities/{activity['id']}/evidence",
        files={"files": ("unsafe.jpg", b"not-an-image", "image/jpeg")},
    )
    assert bad.status_code == 415
    good = await client.post(
        f"/api/alc/activities/{activity['id']}/evidence",
        files={"files": ("proof.pdf", b"%PDF-1.7\nbody", "application/pdf")},
    )
    assert good.status_code == 201 and len(stored) == 1
    evidence_id = good.json()[0]["id"]
    access = await client.get(f"/api/alc/evidence/{evidence_id}/access")
    assert access.status_code == 200 and access.json()["url"].startswith("https://private.example/")
    assert (await client.post(f"/api/alc/activities/{activity['id']}/submit")).status_code == 200
    assert (await client.get("/api/alc/dashboard")).json()["learners"] == 0
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    assert (await client.get("/api/admin/verification-queue?queue_only=true")).json()["total"] == 1
    assert (
        await client.post(f"/api/admin/activities/{activity['id']}/verify", json={})
    ).status_code == 200
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    await login(client, "00010001", "StrongAlcPassA!", "ALC")
    dashboard = (await client.get("/api/alc/dashboard")).json()
    assert dashboard["learners"] == 25 and dashboard["leads"] == 10
