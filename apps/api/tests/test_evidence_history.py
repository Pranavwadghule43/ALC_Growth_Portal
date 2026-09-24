"""Historical evidence traceability.

Evidence that was part of a submission and is later removed by the ALC during a correction:
  - is hidden from the current (active) evidence list,
  - keeps its DB row and stored file,
  - is listed as removed/historical evidence and recorded in the revision snapshot,
  - can still be opened by an authorised reviewer (same ALC/SBU/DCU/Admin scope),
  - stays invisible to other ALCs, other SBUs and other DCUs.
Snapshots that predate evidence recording are left as they are and handled gracefully.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.models import ActivityRevision
from app.routes import portal as portal_routes
from tests.conftest import login
from tests.test_dcu_hierarchy import NASHIK_ALC_SBU4, hier  # noqa: F401  (hierarchy fixture)
from tests.test_dcu_hierarchy import PW as HIER_PW

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
ALC_A = ("00010001", "StrongAlcPassA!", "ALC")
ALC_B = ("00010002", "StrongAlcPassB!", "ALC")
SBU_4 = ("sbu-4", "StrongSbuPass4!", "SBU")
SBU_6 = ("sbu-6", "StrongSbuPass6!", "SBU")
ADMIN = ("admin", "StrongAdminPass!", "ADMIN")
DCU_PN = ("dcu-pn", HIER_PW, "DCU")
DCU_NASHIK = ("dcu-nashik", HIER_PW, "DCU")
SBU_7 = ("sbu-7", HIER_PW, "SBU")
NASHIK_ALC = (NASHIK_ALC_SBU4, HIER_PW, "ALC")


@pytest.fixture
def store(monkeypatch):
    """In-memory evidence storage served through the local (authorised) content route."""
    files: dict[str, bytes] = {}

    async def upload(key, body, content_type):
        files[key] = body

    async def get(key):
        if key not in files:
            raise FileNotFoundError(key)
        return files[key]

    async def delete(key):
        files.pop(key, None)

    service = portal_routes.storage_service
    monkeypatch.setattr(service, "upload", upload)
    monkeypatch.setattr(service, "get", get)
    monkeypatch.setattr(service, "delete", delete)
    monkeypatch.setattr(portal_routes.settings, "storage_backend", "local")
    return files


async def as_user(client, who):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    await login(client, *who)


async def upload(client, activity_id, name):
    resp = await client.post(
        f"/api/portal/activities/{activity_id}/evidence",
        files={"files": (name, f"%PDF-1.7\n{name}".encode(), "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()[0]["id"]


async def corrected_activity(client, reviewer, before_resubmit=None):
    """Submit with old.pdf + kept.pdf, reviewer requests correction, ALC swaps old for new."""
    await as_user(client, ALC_A)
    activity_id = (await client.post("/api/portal/activities", json=ACTIVITY)).json()["id"]
    ids = {
        "old": await upload(client, activity_id, "old.pdf"),
        "kept": await upload(client, activity_id, "kept.pdf"),
    }
    assert (await client.post(f"/api/portal/activities/{activity_id}/submit")).status_code == 200
    if before_resubmit:
        await before_resubmit(activity_id)

    await as_user(client, reviewer)
    resp = await client.post(
        f"/api/portal/activities/{activity_id}/request-correction",
        json={"remark": "Replace old.pdf with the signed copy"},
    )
    assert resp.status_code == 200, resp.text

    await as_user(client, ALC_A)
    assert (await client.delete(f"/api/portal/evidence/{ids['old']}")).status_code == 200
    ids["new"] = await upload(client, activity_id, "new.pdf")
    assert (await client.post(f"/api/portal/activities/{activity_id}/submit")).status_code == 200
    return activity_id, ids


def names(items):
    return sorted(item["original_filename"] for item in items)


async def assert_can_open(client, prefix, evidence_id, body):
    assert (await client.get(f"/api/{prefix}/evidence/{evidence_id}/access")).status_code == 200
    content = await client.get(f"/api/{prefix}/evidence/{evidence_id}/content")
    assert content.status_code == 200 and content.content == body


async def assert_cannot_open(client, evidence_id):
    assert (await client.get(f"/api/portal/evidence/{evidence_id}/access")).status_code == 404
    assert (await client.get(f"/api/portal/evidence/{evidence_id}/content")).status_code == 404


@pytest.mark.asyncio
async def test_removed_evidence_hidden_from_current_but_kept_in_history(client, seeded, store):
    activity_id, ids = await corrected_activity(client, SBU_4)
    detail = (await client.get(f"/api/portal/activities/{activity_id}")).json()

    # Current evidence no longer shows the removed file...
    assert names(detail["evidence"]) == ["kept.pdf", "new.pdf"]
    # ...but it is listed as removed evidence from submission 1, and its file is kept.
    assert [(e["id"], e["submitted_in"], e["recorded"]) for e in detail["removed_evidence"]] == [
        (ids["old"], [1], True)
    ]
    assert any(key.endswith(f"{ids['old']}.pdf") for key in store)
    # Each submission snapshot records exactly what the reviewer was sent.
    first, second = detail["revisions"]
    assert names(first["snapshot"]["evidence"]) == ["kept.pdf", "old.pdf"]
    assert names(second["snapshot"]["evidence"]) == ["kept.pdf", "new.pdf"]
    # Removed evidence is read-only history: it cannot be removed again.
    assert (await client.delete(f"/api/portal/evidence/{ids['old']}")).status_code == 404


@pytest.mark.asyncio
async def test_authorised_reviewers_can_open_removed_evidence(client, seeded, store):
    activity_id, ids = await corrected_activity(client, SBU_4)
    old = b"%PDF-1.7\nold.pdf"

    await as_user(client, SBU_4)  # the centre's own SBU
    detail = (await client.get(f"/api/portal/verification/{activity_id}")).json()
    assert [e["id"] for e in detail["activity"]["removed_evidence"]] == [ids["old"]]
    await assert_can_open(client, "portal", ids["old"], old)

    await as_user(client, ADMIN)
    detail = (await client.get(f"/api/admin/activities/{activity_id}")).json()
    assert [e["id"] for e in detail["activity"]["removed_evidence"]] == [ids["old"]]
    await assert_can_open(client, "admin", ids["old"], old)

    await as_user(client, ALC_A)  # the owning ALC keeps read access too
    await assert_can_open(client, "portal", ids["old"], old)


@pytest.mark.asyncio
async def test_removed_evidence_is_not_visible_cross_alc_or_cross_sbu(client, seeded, store):
    activity_id, ids = await corrected_activity(client, SBU_4)
    for who in (SBU_6, ALC_B):
        await as_user(client, who)
        assert (await client.get(f"/api/portal/activities/{activity_id}")).status_code == 404
        await assert_cannot_open(client, ids["old"])
        await assert_cannot_open(client, ids["kept"])


@pytest.mark.asyncio
async def test_removed_evidence_follows_dcu_scope(client, hier, store):  # noqa: F811
    # In the hierarchy fixture Centre A belongs to TEST PN under DCU Pune North.
    activity_id, ids = await corrected_activity(client, DCU_PN)
    old = b"%PDF-1.7\nold.pdf"

    await as_user(client, DCU_PN)
    detail = (await client.get(f"/api/portal/verification/{activity_id}")).json()
    assert [e["id"] for e in detail["activity"]["removed_evidence"]] == [ids["old"]]
    await assert_can_open(client, "portal", ids["old"], old)

    for who in (DCU_NASHIK, SBU_7, NASHIK_ALC):  # another DCU, an SBU and an ALC under it
        await as_user(client, who)
        assert (await client.get(f"/api/portal/activities/{activity_id}")).status_code == 404
        await assert_cannot_open(client, ids["old"])


@pytest.mark.asyncio
async def test_evidence_never_submitted_is_deleted_not_historical(client, seeded, store):
    activity_id, ids = await corrected_activity(client, SBU_4)
    await as_user(client, SBU_4)
    resp = await client.post(
        f"/api/portal/activities/{activity_id}/request-correction",
        json={"remark": "One more fix"},
    )
    assert resp.status_code == 200
    await as_user(client, ALC_A)
    draft_only = await upload(client, activity_id, "mistake.pdf")
    assert (await client.delete(f"/api/portal/evidence/{draft_only}")).status_code == 200

    # No reviewer was ever sent mistake.pdf: the file is deleted and it is not history.
    assert not any(key.endswith(f"{draft_only}.pdf") for key in store)
    detail = (await client.get(f"/api/portal/activities/{activity_id}")).json()
    assert [e["id"] for e in detail["removed_evidence"]] == [ids["old"]]
    await as_user(client, SBU_4)
    await assert_cannot_open(client, draft_only)


@pytest.mark.asyncio
async def test_old_snapshots_without_evidence_are_handled(client, session, seeded, store):
    async def make_legacy(activity_id):
        # Simulate history written before evidence was recorded in snapshots.
        revision = await session.scalar(
            select(ActivityRevision).where(ActivityRevision.activity_id == uuid.UUID(activity_id))
        )
        snapshot = dict(revision.snapshot)
        snapshot.pop("evidence")
        revision.snapshot = snapshot
        await session.commit()

    activity_id, ids = await corrected_activity(client, SBU_4, before_resubmit=make_legacy)
    detail = (await client.get(f"/api/portal/activities/{activity_id}")).json()

    # The old snapshot is not rewritten; the removed file is still found (inferred from upload
    # time) and flagged as not recorded, and the reviewer can still open it.
    first, second = detail["revisions"]
    assert "evidence" not in first["snapshot"]
    assert names(second["snapshot"]["evidence"]) == ["kept.pdf", "new.pdf"]
    assert [(e["id"], e["submitted_in"], e["recorded"]) for e in detail["removed_evidence"]] == [
        (ids["old"], [1], False)
    ]
    await as_user(client, SBU_4)
    await assert_can_open(client, "portal", ids["old"], b"%PDF-1.7\nold.pdf")