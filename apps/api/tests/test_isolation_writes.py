"""Cross-tenant isolation for ALC *write* endpoints.

The read side (activities, verification, evidence access, partners, reports, reset-password)
is covered by test_sbu_isolation / test_dcu_hierarchy / test_dcu_portal. These tests cover
the ALC-owned write actions:
  * one ALC can never change another ALC's activity, evidence, partner, task or notification
    (the record must look like it does not exist: 404);
  * supervisors (SBU / DCU) can review but never author or edit ALC content (403).
"""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models import ActivityEvidence, Notification, User
from tests.conftest import login
from tests.test_dcu_hierarchy import PW as HIER_PW
from tests.test_dcu_hierarchy import hier  # noqa: F401  (hierarchy fixture)

ALC_A = ("00010001", "StrongAlcPassA!")  # Centre A, SBU 4
ALC_B = ("00010002", "StrongAlcPassB!")  # Centre B, SBU 6

ACTIVITY = {
    "activity_type": "Partner meeting",
    "ecosystem": "College",
    "collaboration_type": "Pilot discussion",
    "activity_date": str(date.today()),
    "location": "Pune",
    "learners_reached": 10,
    "leads_generated": 2,
    "admissions_generated": 0,
    "description": "Discussed a structured learner outreach pilot.",
    "outcome": "Pilot agreed",
}
PARTNER = {"partner_name": "Beta College", "partner_type": "College", "ecosystem": "College"}
PDF = b"%PDF-1.4\n%test\n"


def logout(client):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)


async def as_login(client, identifier, password, portal="ALC"):
    logout(client)
    resp = await login(client, identifier, password, portal)
    assert resp.status_code == 200, resp.text


async def centre_b_records(client, session):
    """As ALC B: a draft activity with evidence, a partner, a task and a notification."""
    await as_login(client, *ALC_B)
    activity = (await client.post("/api/portal/activities", json=ACTIVITY)).json()
    partner = (await client.post("/api/portal/partners", json=PARTNER)).json()
    task = (
        await client.post(
            "/api/portal/tasks",
            json={"title": "Follow up", "due_date": str(date.today() + timedelta(days=3))},
        )
    ).json()
    user_b = await session.scalar(select(User).where(User.username == "alc-b"))
    evidence = ActivityEvidence(
        activity_id=uuid.UUID(activity["id"]),
        storage_key=f"private/{uuid.uuid4()}.pdf",
        original_filename="proof.pdf",
        mime_type="application/pdf",
        file_size=len(PDF),
        uploaded_by=user_b.id,
    )
    note = Notification(user_id=user_b.id, title="Hello", message="For B only", type="INFO")
    session.add_all([evidence, note])
    await session.commit()
    return {
        "activity": activity["id"],
        "partner": partner["id"],
        "task": task["id"],
        "evidence": str(evidence.id),
        "notification": str(note.id),
    }


def write_calls(ids):
    """Every ALC-owned write endpoint, aimed at another centre's records."""
    return [
        ("patch", f"/api/portal/activities/{ids['activity']}", {"json": ACTIVITY}),
        ("post", f"/api/portal/activities/{ids['activity']}/submit", {}),
        (
            "post",
            f"/api/portal/activities/{ids['activity']}/evidence",
            {"files": {"files": ("x.pdf", PDF, "application/pdf")}},
        ),
        ("delete", f"/api/portal/evidence/{ids['evidence']}", {}),
        ("patch", f"/api/portal/partners/{ids['partner']}", {"json": {**PARTNER, "notes": "x"}}),
        ("patch", f"/api/portal/tasks/{ids['task']}/complete", {}),
    ]


async def assert_centre_b_untouched(client, ids):
    await as_login(client, *ALC_B)
    activity = (await client.get(f"/api/portal/activities/{ids['activity']}")).json()
    assert activity["status"] == "DRAFT"
    assert activity["learners_reached"] == ACTIVITY["learners_reached"]
    assert [e["id"] for e in activity["evidence"]] == [ids["evidence"]]
    partners = {p["id"]: p for p in (await client.get("/api/portal/partners")).json()}
    assert partners[ids["partner"]].get("notes") in (None, "")
    tasks = {t["id"]: t for t in (await client.get("/api/portal/tasks")).json()}
    assert tasks[ids["task"]]["status"] == "OPEN"


@pytest.mark.asyncio
async def test_alc_cannot_write_another_alcs_records(client, session, seeded):
    ids = await centre_b_records(client, session)
    await as_login(client, *ALC_A)
    for method, path, kwargs in write_calls(ids):
        resp = await getattr(client, method)(path, **kwargs)
        assert resp.status_code == 404, (method, path, resp.status_code, resp.text)
    # Another user's notification cannot be marked read either.
    resp = await client.post(f"/api/portal/notifications/{ids['notification']}/read")
    assert resp.status_code == 404
    await assert_centre_b_untouched(client, ids)
    note = await session.get(Notification, uuid.UUID(ids["notification"]))
    await session.refresh(note)
    assert note.is_read is False


@pytest.mark.asyncio
async def test_sbu_can_review_but_never_author_alc_content(client, session, seeded):
    ids = await centre_b_records(client, session)
    # Centre B belongs to SBU 6: its own SBU is still refused on every ALC write.
    await as_login(client, "sbu-6", "StrongSbuPass6!", "SBU")
    for method, path, kwargs in write_calls(ids):
        resp = await getattr(client, method)(path, **kwargs)
        assert resp.status_code == 403, (method, path, resp.status_code, resp.text)
    assert (await client.post("/api/portal/activities", json=ACTIVITY)).status_code == 403
    assert (await client.post("/api/portal/partners", json=PARTNER)).status_code == 403
    await assert_centre_b_untouched(client, ids)


@pytest.mark.asyncio
async def test_dcu_can_review_but_never_author_alc_content(client, session, hier):  # noqa: F811
    ids = await centre_b_records(client, session)
    # Centre B sits under DCU Pune North in the hierarchy fixture: its own DCU is refused too.
    await as_login(client, "dcu-pn", HIER_PW, "DCU")
    for method, path, kwargs in write_calls(ids):
        resp = await getattr(client, method)(path, **kwargs)
        assert resp.status_code == 403, (method, path, resp.status_code, resp.text)
    assert (await client.post("/api/portal/activities", json=ACTIVITY)).status_code == 403
    await assert_centre_b_untouched(client, ids)