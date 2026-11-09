"""Evidence-backed yearly activity acceptance through actual role/scoped API endpoints."""

import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from app.enums import Role
from app.models import DCU, AuditLog, User, YearlyActivityReview
from app.services import yearly_activities as activities
from app.services import yearly_submissions as submissions
from tests.conftest import login
from tests.evidence_storage import MemoryStorage
from tests.test_activity_exports import StatementLog

ROOT = "/api/yearly-activities"
PNG = b"\x89PNG\r\n\x1a\nsynthetic image proof"
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42" + b"synthetic video proof"
WEBM = b"\x1a\x45\xdf\xa3\x9fwebm synthetic video proof"
DATA = {
    "completion_date": "2026-11-02",
    "remarks": "Completed the regional activity with evidence.",
}


async def switch(client, who="alc"):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    credentials = {
        "alc": ("00010001", "StrongAlcPassA!", "ALC"),
        "other_alc": ("00010002", "StrongAlcPassB!", "ALC"),
        "sbu": ("sbu-4", "StrongSbuPass4!", "SBU"),
        "other_sbu": ("sbu-6", "StrongSbuPass6!", "SBU"),
        "admin": ("admin", "StrongAdminPass!", "ADMIN"),
        "dcu": ("proof-dcu", "StrongAdminPass!", "DCU"),
        "other_dcu": ("proof-other-dcu", "StrongAdminPass!", "DCU"),
    }
    response = await login(client, *credentials[who])
    assert response.status_code == 200, response.text


@pytest.fixture
async def proof(client, session, seeded, monkeypatch):
    monkeypatch.setattr(activities, "local_today", lambda: date(2026, 11, 4))
    monkeypatch.setattr(submissions, "current_date", lambda: date(2026, 11, 4))
    storage = MemoryStorage(monkeypatch)
    dcus = (await session.scalars(select(DCU))).all()
    own = next(d for d in dcus if d.id == seeded["sbu4"].dcu_id)
    other = next(d for d in dcus if d.id != own.id)
    for dcu in dcus:
        dcu.is_active = dcu.id in {own.id, other.id}
    session.add_all(
        [
            User(
                username="proof-dcu",
                role=Role.DCU,
                dcu_id=own.id,
                password_hash=seeded["admin"].password_hash,
            ),
            User(
                username="proof-other-dcu",
                role=Role.DCU,
                dcu_id=other.id,
                password_hash=seeded["admin"].password_hash,
            ),
        ]
    )
    await session.commit()
    await switch(client, "dcu")
    assert (
        await client.post(
            f"{ROOT}/definitions",
            json={
                "calendar_year": 2026,
                "activity_code": "PROOF",
                "name": "Regional evidence event",
            },
        )
    ).status_code == 201
    registry = (await client.get(f"{ROOT}/definitions?year=2026")).json()
    run = registry["items"][0]["runs"][0]["id"]
    for data in (
        {"scheduled_start_date": "2026-11-01", "scheduled_end_date": "2026-11-04"},
        {"status": "ACTIVE"},
    ):
        assert (await client.patch(f"{ROOT}/runs/{run}", json=data)).status_code == 200
    await switch(client)
    return {
        "run": run,
        "storage": storage,
        "other_dcu": other,
        "own_dcu": own,
        "activity": registry["items"][0]["id"],
    }


async def draft(client, proof):
    response = await client.post(f"{ROOT}/runs/{proof['run']}/submission", json=DATA)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_submission_list_derives_total_from_its_status_summary(client, proof, session):
    await switch(client, "dcu")
    with StatementLog(session) as log:
        response = await client.get(f"{ROOT}/submissions?year=2026")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == sum(body["summary"].values()) == len(body["items"])
    endpoint_queries = [statement for statement in log.statements if "FROM users" not in statement]
    # The status summary supplies total; page and page annual counts are the remaining queries.
    assert len(endpoint_queries) == 3


async def upload(client, sub, body=PNG, name="proof.png", mime="image/png"):
    return await client.post(
        f"{ROOT}/submissions/{sub}/evidence", files={"files": (name, body, mime)}
    )


async def submitted(client, proof):
    sub = await draft(client, proof)
    evidence = await upload(client, sub)
    assert evidence.status_code == 201, evidence.text
    response = await client.post(f"{ROOT}/submissions/{sub}/submit")
    assert response.status_code == 200, response.text
    return sub, evidence.json()["id"]


async def decision(client, sub, action="VERIFY", revision=1, remarks=None):
    return await client.post(
        f"{ROOT}/submissions/{sub}/review",
        json={"decision": action, "revision": revision, "remarks": remarks},
    )


async def test_own_draft_and_cross_alc_and_outside_run_denied(client, proof, seeded):
    sub = await draft(client, proof)
    assert (await client.get(f"{ROOT}/submissions/{sub}")).json()["status"] == "DRAFT"
    assert (
        await client.post(f"{ROOT}/runs/{proof['run']}/submission", json=DATA)
    ).status_code == 409
    assert (
        await client.post(f"{ROOT}/runs/{uuid.uuid4()}/submission", json=DATA)
    ).status_code == 404
    await switch(client, "other_alc")
    assert (await client.get(f"{ROOT}/submissions/{sub}")).status_code == 404
    assert (await client.patch(f"{ROOT}/submissions/{sub}", json=DATA)).status_code == 404


@pytest.mark.parametrize(
    "body,name,mime",
    [
        (PNG, "a.png", "image/png"),
        (b"\xff\xd8\xffproof", "a.jpg", "image/jpeg"),
        (b"RIFFxxxxWEBPproof", "a.webp", "image/webp"),
        (MP4, "a.mp4", "video/mp4"),
        (WEBM, "a.webm", "video/webm"),
    ],
)
async def test_supported_image_video_types_and_private_metadata(client, proof, body, name, mime):
    sub = await draft(client, proof)
    result = await upload(client, sub, body, name, mime)
    assert result.status_code == 201, result.text
    assert result.json()["mime_type"] == mime
    assert "storage_key" not in result.json()
    file_id = result.json()["id"]
    content = await client.get(f"{ROOT}/evidence/{file_id}/content")
    assert content.content == body and content.headers["cache-control"] == "private, no-store"
    part = await client.get(f"{ROOT}/evidence/{file_id}/content", headers={"Range": "bytes=1-4"})
    assert part.status_code == 206 and part.content == body[1:5]


async def test_evidence_validation_size_count_and_required_evidence(client, proof, monkeypatch):
    sub = await draft(client, proof)
    assert (await client.post(f"{ROOT}/submissions/{sub}/submit")).status_code == 422
    assert (await upload(client, sub, b"<script>bad</script>")).status_code == 415
    assert (await upload(client, sub, PNG, "bad.exe", "image/png")).status_code == 415
    monkeypatch.setattr(submissions.settings, "max_upload_bytes", 12)
    assert (await upload(client, sub, PNG)).status_code == 413
    assert (await upload(client, sub, b"")).status_code == 413


async def test_correction_resubmission_review_history_and_dcu_finalization(
    client, proof, session
):
    sub, original = await submitted(client, proof)
    await switch(client, "dcu")
    assert (await decision(client, sub, "REQUEST_CORRECTION")).status_code == 422
    corrected = await decision(client, sub, "REQUEST_CORRECTION", remarks="Provide video proof")
    assert corrected.status_code == 200, corrected.text
    await switch(client)
    assert (await client.delete(f"{ROOT}/evidence/{original}")).status_code == 200
    added = await upload(client, sub, MP4, "new.mp4", "video/mp4")
    assert added.status_code == 201
    await switch(client, "dcu")
    assert (await client.get(f"{ROOT}/evidence/{added.json()['id']}/content")).status_code == 404
    assert (await client.get(f"{ROOT}/evidence/{original}/content")).status_code == 200
    await switch(client)
    assert (
        await client.patch(
            f"{ROOT}/submissions/{sub}",
            json={**DATA, "remarks": "Corrected submission with a new video"},
        )
    ).status_code == 200
    resub = await client.post(f"{ROOT}/submissions/{sub}/submit")
    assert resub.json()["status"] == "RESUBMITTED" and resub.json()["revision"] == 2
    await switch(client, "dcu")
    verified = await decision(client, sub, revision=2)
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "VERIFIED" and len(verified.json()["reviews"]) == 2
    assert verified.json()["reviews"][0]["snapshot"]["remarks"] == DATA["remarks"]
    assert (await decision(client, sub, revision=2)).status_code == 409
    assert (await client.get("/api/leaderboard/region-scores?year=2026")).json()["items"] == []
    assert (
        await client.patch(f"{ROOT}/runs/{proof['run']}", json={"status": "MARKING"})
    ).status_code == 200
    assert (await client.post(f"{ROOT}/runs/{proof['run']}/finalize")).status_code == 200
    scores = (await client.get("/api/leaderboard/region-scores?year=2026")).json()
    assert scores["items"][0]["yearly_completed_count"] == 1
    assert scores["items"][0]["score"] == 20
    await switch(client)
    assert (await client.patch(f"{ROOT}/submissions/{sub}", json=DATA)).status_code == 409
    assert (await upload(client, sub)).status_code == 409
    assert await session.scalar(select(func.count(YearlyActivityReview.id))) == 2
    assert await session.scalar(
        select(AuditLog.id).where(AuditLog.action == "yearly_activity_verified")
    )


@pytest.mark.parametrize("who", ["other_alc", "other_sbu", "other_dcu"])
async def test_cross_scope_submission_and_evidence_fail(client, proof, who):
    sub, evidence = await submitted(client, proof)
    await switch(client, who)
    assert (await client.get(f"{ROOT}/submissions/{sub}")).status_code == 404
    assert (await client.get(f"{ROOT}/evidence/{evidence}/content")).status_code == 404
    if who == "other_dcu":
        assert (await decision(client, sub)).status_code == 404


@pytest.mark.parametrize("who", ["alc", "sbu", "admin"])
async def test_non_dcu_cannot_review_and_sbu_admin_readonly(client, proof, who):
    sub, evidence = await submitted(client, proof)
    await switch(client, who)
    assert (await client.get(f"{ROOT}/submissions/{sub}")).status_code == 200
    assert (await client.get(f"{ROOT}/evidence/{evidence}/content")).status_code == 200
    assert (await decision(client, sub)).status_code == 403
    if who != "alc":
        assert (await client.patch(f"{ROOT}/submissions/{sub}", json=DATA)).status_code == 403


async def test_rejection_final_and_cannot_fabricate_completion(client, proof, session):
    sub, _ = await submitted(client, proof)
    await switch(client, "dcu")
    assert (await decision(client, sub, "REJECT")).status_code == 422
    rejected = await decision(client, sub, "REJECT", remarks="Evidence does not show the activity")
    assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED"
    assert (
        await client.patch(
            f"{ROOT}/runs/{proof['run']}/completions/{uuid.uuid4()}", json={"completed": True}
        )
    ).status_code == 410
    await switch(client)
    assert (await client.post(f"{ROOT}/submissions/{sub}/submit")).status_code == 409
    assert (await client.get("/api/leaderboard/region-scores?year=2026")).json()["items"] == []


async def test_pending_reviews_block_close_and_drafts_stay_private(client, proof):
    sub = await draft(client, proof)
    await upload(client, sub)
    await switch(client, "dcu")
    assert (await client.get(f"{ROOT}/submissions/{sub}")).status_code == 404
    await switch(client)
    assert (await client.post(f"{ROOT}/submissions/{sub}/submit")).status_code == 200
    await switch(client, "dcu")
    assert (
        await client.patch(f"{ROOT}/runs/{proof['run']}", json={"status": "MARKING"})
    ).status_code == 200
    assert (await client.post(f"{ROOT}/runs/{proof['run']}/finalize")).status_code == 409
    listing = (
        await client.get(f"{ROOT}/submissions?year=2026&status=SUBMITTED&page_size=1")
    ).json()
    assert listing["total"] == 1 and listing["items"][0]["submission_id"] == sub


async def test_storage_failure_is_retryable_without_evidence_metadata(client, proof, monkeypatch):
    sub = await draft(client, proof)

    async def unavailable(*args):
        raise ConnectionError("synthetic unavailable store")

    monkeypatch.setattr(submissions.storage_service, "upload", unavailable)
    assert (await upload(client, sub)).status_code == 503
    assert (await client.get(f"{ROOT}/submissions/{sub}")).json()["evidence"] == []


async def test_chunked_body_limit_prevents_multipart_spooling(client, proof, monkeypatch):
    sub = await draft(client, proof)
    monkeypatch.setattr(submissions.settings, "max_upload_bytes", 1024)

    async def chunks():
        yield b'--boundary\r\nContent-Disposition: form-data; name="files"; filename="x.png"\r\n'
        yield b"Content-Type: image/png\r\n\r\n" + PNG
        for _ in range(140):
            yield b"x" * 1024
        yield b"\r\n--boundary--\r\n"

    response = await client.post(
        f"{ROOT}/submissions/{sub}/evidence",
        content=chunks(),
        headers={"Content-Type": "multipart/form-data; boundary=boundary"},
    )
    assert response.status_code == 413
    assert (await client.get(f"{ROOT}/submissions/{sub}")).json()["evidence"] == []


async def test_slow_request_body_has_a_total_deadline(client, proof, monkeypatch):
    from app import upload_limits

    sub = await draft(client, proof)
    monkeypatch.setattr(upload_limits, "REQUEST_BODY_TIMEOUT", 0.01)

    async def slow():
        await asyncio.sleep(0.05)
        yield b"--boundary--\r\n"

    response = await client.post(
        f"{ROOT}/submissions/{sub}/evidence",
        content=slow(),
        headers={"Content-Type": "multipart/form-data; boundary=boundary"},
    )
    assert response.status_code == 408
