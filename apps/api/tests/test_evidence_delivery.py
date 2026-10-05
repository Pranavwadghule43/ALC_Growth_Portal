"""Evidence is delivered through the authenticated API, never through a storage URL.

* ``GET /api/portal/evidence/{id}/content`` (DCU / SBU / ALC) and
  ``GET /api/admin/evidence/{id}/content`` (ADMIN) stream the private object for S3/MinIO and
  local storage alike, after the existing activity scope check.
* ``.../access`` returns that application URL (root-relative), never a presigned storage URL.
* Responses never carry the bucket, object key, filesystem path or storage endpoint.
* Objects are streamed in chunks and always closed: completed, failed or disconnected.
"""
import uuid
from datetime import date
from pathlib import Path

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from sqlalchemy import select
from starlette.requests import ClientDisconnect

from app.auth import hash_password
from app.config import settings
from app.enums import Role
from app.models import DCU, ActivityEvidence, User
from app.routes.portal import ALLOWED_MIMES
from app.services import evidence_delivery
from app.services.evidence_delivery import (
    PREVIEWABLE_TYPES,
    content_disposition,
    content_type,
    evidence_response,
    safe_filename,
)
from app.storage import storage_service
from app.storage.base import StorageObjectNotFound
from app.storage.local import LocalStorageService
from app.storage.s3 import S3StorageService
from tests.conftest import login
from tests.evidence_storage import MemoryObjectStream, MemoryStorage

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
DCU_PW = "StrongDcuPass123!"
ALC_A = ("00010001", "StrongAlcPassA!", "ALC")
ALC_B = ("00010002", "StrongAlcPassB!", "ALC")
SBU_4 = ("sbu-4", "StrongSbuPass4!", "SBU")  # Centre A's SBU
SBU_6 = ("sbu-6", "StrongSbuPass6!", "SBU")
DCU_NASHIK = ("dcu-nashik", DCU_PW, "DCU")  # SBU 4 and SBU 6 sit under DCU Nashik
DCU_PUNE_NORTH = ("dcu-pune-north", DCU_PW, "DCU")
ADMIN = ("admin", "StrongAdminPass!", "ADMIN")

PDF = b"%PDF-1.7\n" + b"evidence page " * 20 + b"\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(64))
STORAGE_LEAKS = ("127.0.0.1", "localhost:9000", ":9000", "minio", "X-Amz", "Signature=")


@pytest.fixture
def storage(monkeypatch):
    return MemoryStorage(monkeypatch)


@pytest.fixture
async def world(client, session, seeded, storage):
    dcus = {d.code: d for d in (await session.scalars(select(DCU))).all()}
    session.add_all([
        User(username="dcu-nashik", password_hash=hash_password(DCU_PW), role=Role.DCU,
             dcu_id=dcus["DCU_NASHIK"].id),
        User(username="dcu-pune-north", password_hash=hash_password(DCU_PW), role=Role.DCU,
             dcu_id=dcus["DCU_PUNE_NORTH"].id),
    ])
    await session.commit()

    await as_user(client, ALC_A)
    submitted = (await client.post("/api/portal/activities", json=ACTIVITY)).json()["id"]
    pdf_id = await upload(client, submitted, "proof.pdf", PDF, "application/pdf")
    png_id = await upload(client, submitted, "photo.png", PNG, "image/png")
    assert (await client.post(f"/api/portal/activities/{submitted}/submit")).status_code == 200
    draft = (await client.post("/api/portal/activities", json=ACTIVITY)).json()["id"]
    draft_id = await upload(client, draft, "draft.pdf", PDF, "application/pdf")
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    return {"pdf": pdf_id, "png": png_id, "draft": draft_id, "activity": submitted}


async def as_user(client, who):
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    assert (await login(client, *who)).status_code == 200


async def upload(client, activity_id, name, body, mime):
    resp = await client.post(
        f"/api/portal/activities/{activity_id}/evidence", files={"files": (name, body, mime)}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()[0]["id"]


def content_url(prefix, evidence_id):
    return f"/api/{prefix}/evidence/{evidence_id}/content"


def assert_no_storage_details(response, storage):
    text = response.text + "".join(f"{k}: {v}\n" for k, v in response.headers.items())
    for leak in (*STORAGE_LEAKS, settings.s3_bucket, *storage.files):
        assert leak not in text


# --------------------------------------------------------------------------- #
# Authorization: the existing activity scope decides, before storage is touched
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("who", "prefix"),
    [(ALC_A, "portal"), (SBU_4, "portal"), (DCU_NASHIK, "portal"), (ADMIN, "admin")],
    ids=["own-alc", "sbu", "dcu", "admin"],
)
async def test_authorized_users_receive_the_file(client, world, storage, who, prefix):
    await as_user(client, who)
    access = await client.get(f"/api/{prefix}/evidence/{world['pdf']}/access")
    assert access.status_code == 200
    # The browser is sent to the application route, never to object storage.
    assert access.json() == {"url": content_url(prefix, world["pdf"])}

    response = await client.get(access.json()["url"])
    assert response.status_code == 200
    assert response.content == PDF
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        "inline; filename=\"proof.pdf\"; filename*=UTF-8''proof.pdf"
    )
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-length"] == str(len(PDF))
    assert response.headers["x-content-type-options"] == "nosniff"
    assert_no_storage_details(response, storage)
    assert storage.opened[-1].closed


@pytest.mark.asyncio
@pytest.mark.parametrize("who", [ALC_B, SBU_6, DCU_PUNE_NORTH], ids=["alc", "sbu", "dcu"])
async def test_cross_scope_requests_are_not_found(client, world, storage, who):
    await as_user(client, who)
    for evidence_id in (world["pdf"], world["png"], world["draft"]):
        for path in ("access", "content"):
            response = await client.get(f"/api/portal/evidence/{evidence_id}/{path}")
            assert response.status_code == 404
            assert response.json() == {"detail": "Evidence not found"}
    assert storage.opened == []  # unauthorized requests never reach storage


@pytest.mark.asyncio
async def test_draft_evidence_is_only_for_its_own_alc(client, world, storage):
    await as_user(client, ALC_A)
    assert (await client.get(content_url("portal", world["draft"]))).content == PDF
    for who, prefix in ((SBU_4, "portal"), (DCU_NASHIK, "portal"), (ADMIN, "admin")):
        await as_user(client, who)
        assert (await client.get(content_url(prefix, world["draft"]))).status_code == 404
        assert (
            await client.get(f"/api/{prefix}/evidence/{world['draft']}/access")
        ).status_code == 404
    assert len(storage.opened) == 1


@pytest.mark.asyncio
async def test_portal_and_admin_routes_keep_their_roles(client, world):
    await as_user(client, ADMIN)
    response = await client.get(content_url("portal", world["pdf"]))
    assert response.status_code == 403 and response.json()["detail"] == "Portal access required"
    for who in (ALC_A, SBU_4, DCU_NASHIK):
        await as_user(client, who)
        response = await client.get(content_url("admin", world["pdf"]))
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_inactive_and_password_change_users(
    client, session, seeded, world, storage
):
    for prefix in ("portal", "admin"):
        response = await client.get(content_url(prefix, world["pdf"]))
        assert response.status_code == 401

    await as_user(client, ALC_A)
    seeded["a"].must_change_password = True
    await session.commit()
    response = await client.get(content_url("portal", world["pdf"]))
    assert response.status_code == 403
    assert response.json()["detail"] == "Password change required"

    seeded["a"].must_change_password = False
    seeded["a"].is_active = False
    await session.commit()
    response = await client.get(content_url("portal", world["pdf"]))
    assert response.status_code == 401
    assert storage.opened == []


@pytest.mark.asyncio
async def test_query_string_tokens_are_not_authentication(client, world):
    await as_user(client, ALC_A)
    token = client.cookies.get("access_token")
    client.cookies.clear()
    url = f"{content_url('portal', world['pdf'])}?access_token={token}&token={token}"
    assert (await client.get(url)).status_code == 401


# --------------------------------------------------------------------------- #
# File delivery
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_image_is_served_inline_with_its_detected_type(client, world):
    await as_user(client, SBU_4)
    response = await client.get(content_url("portal", world["png"]))
    assert response.status_code == 200 and response.content == PNG
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"].startswith('inline; filename="photo.png"')


@pytest.mark.asyncio
async def test_missing_metadata_is_not_found(client, world, storage):
    unknown = uuid.uuid4()
    await as_user(client, ALC_A)
    assert (await client.get(content_url("portal", unknown))).status_code == 404
    await as_user(client, ADMIN)
    assert (await client.get(content_url("admin", unknown))).status_code == 404
    assert (await client.get("/api/admin/evidence/not-a-uuid/content")).status_code == 422
    assert storage.opened == []


@pytest.mark.asyncio
async def test_missing_object_is_not_found_without_details(client, world, storage, logs):
    keys = tuple(storage.files)
    storage.files.clear()
    for who, prefix in ((ALC_A, "portal"), (ADMIN, "admin")):
        await as_user(client, who)
        response = await client.get(content_url(prefix, world["pdf"]))
        assert response.status_code == 404
        assert response.json() == {"detail": "Evidence not found"}
        assert "evidence/" not in response.text
    events = logs.named("evidence_object_missing")
    assert len(events) == 2
    assert all(event["evidence_id"] == world["pdf"] for event in events)
    assert all(key not in logs.text for key in keys)


@pytest.mark.asyncio
async def test_storage_failure_is_503_without_leaking_storage_details(
    client, world, storage, logs
):
    key = next(iter(storage.files))
    storage.open_error = EndpointConnectionError(
        endpoint_url=f"http://127.0.0.1:9000/{settings.s3_bucket}/{key}"
    )
    await as_user(client, SBU_4)
    response = await client.get(content_url("portal", world["pdf"]))
    assert response.status_code == 503
    assert response.json() == {"detail": "Evidence is temporarily unavailable"}
    assert_no_storage_details(response, storage)
    [event] = logs.named("evidence_storage_unavailable")
    assert event["evidence_id"] == world["pdf"]
    assert event["error_type"] == "EndpointConnectionError"
    event_text = str(event)
    assert "127.0.0.1" not in event_text
    assert key not in event_text


@pytest.mark.asyncio
async def test_untrusted_stored_type_falls_back_to_a_download(client, session, world, storage):
    # A legacy/tampered row whose stored type is not an accepted evidence type.
    evidence = await session.get(ActivityEvidence, uuid.UUID(world["png"]))
    evidence.mime_type = "text/html"
    evidence.original_filename = "page.html"
    await session.commit()
    await as_user(client, SBU_4)
    response = await client.get(content_url("portal", world["png"]))
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith('attachment; filename="page.html"')


@pytest.mark.asyncio
async def test_unicode_and_hostile_filenames_are_safe_headers(client, world, storage):
    await as_user(client, ALC_A)
    activity = (await client.post("/api/portal/activities", json=ACTIVITY)).json()["id"]
    evidence_id = await upload(
        client, activity, 'प्रमाण "final"\r\nSet-Cookie: x=1.pdf', PDF, "application/pdf"
    )
    response = await client.get(content_url("portal", evidence_id))
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    assert "set-cookie" not in response.headers  # nothing injected as an extra header
    assert disposition.startswith('inline; filename="')
    assert "filename*=UTF-8''" in disposition


def test_activity_json_never_exposes_storage_locations():
    # Evidence in activity responses is described by id, name, type, size and time only.
    from app.schemas import EvidenceOut, RemovedEvidenceOut

    for model in (EvidenceOut, RemovedEvidenceOut):
        assert "storage_key" not in model.model_fields
        assert not any("url" in name for name in model.model_fields)


@pytest.mark.asyncio
async def test_activity_detail_has_no_storage_details(client, world, storage):
    for who, path in (
        (ALC_A, f"/api/portal/activities/{world['activity']}"),
        (SBU_4, f"/api/portal/verification/{world['activity']}"),
        (ADMIN, f"/api/admin/activities/{world['activity']}"),
    ):
        await as_user(client, who)
        response = await client.get(path)
        assert response.status_code == 200
        assert "storage_key" not in response.text
        assert_no_storage_details(response, storage)


# --------------------------------------------------------------------------- #
# Streaming and cleanup
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_route_streams_in_chunks_and_closes(client, world, storage):
    await as_user(client, ALC_A)
    response = await client.get(content_url("portal", world["pdf"]))
    stream = storage.opened[-1]
    assert response.content == PDF
    assert stream.chunks_read == -(-len(PDF) // storage.chunk_size)  # every chunk, one by one
    assert stream.close_calls >= 1


class _Evidence:
    def __init__(self, mime="application/pdf", name="proof.pdf"):
        self.id = uuid.uuid4()
        self.storage_key = "evidence/alc/activity/file.pdf"
        self.mime_type = mime
        self.original_filename = name


@pytest.mark.asyncio
async def test_response_reads_lazily_and_closes_when_abandoned(storage):
    storage.files["evidence/alc/activity/file.pdf"] = PDF
    response = await evidence_response(_Evidence())
    stream = storage.opened[-1]
    assert stream.chunks_read == 0  # nothing is read until the response is sent
    body = response.body_iterator
    first = await body.__anext__()
    assert first == PDF[: storage.chunk_size] and stream.chunks_read == 1
    await body.aclose()  # what happens to the body when the client goes away mid-download
    assert stream.closed and stream.chunks_read == 1


def _scope(spec_version):
    return {"type": "http", "asgi": {"spec_version": spec_version}, "method": "GET",
            "path": "/", "headers": []}


@pytest.mark.asyncio
async def test_client_disconnect_mid_stream_closes_the_object(storage):
    storage.files["evidence/alc/activity/file.pdf"] = PDF
    response = await evidence_response(_Evidence())
    sent = []

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body":
            raise OSError("client went away")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    with pytest.raises(ClientDisconnect):
        await response(_scope("2.4"), receive, send)
    assert storage.opened[-1].closed


@pytest.mark.asyncio
async def test_disconnect_before_streaming_starts_still_closes(storage):
    storage.files["evidence/alc/activity/file.pdf"] = PDF
    response = await evidence_response(_Evidence())

    async def send(message):
        pass

    async def receive():
        return {"type": "http.disconnect"}

    await response(_scope("2.0"), receive, send)
    assert storage.opened[-1].closed


@pytest.mark.asyncio
async def test_failure_mid_stream_closes_the_object(client, world, storage):
    storage.fail_after = 1
    await as_user(client, ALC_A)
    with pytest.raises(ConnectionError):
        await client.get(content_url("portal", world["pdf"]))
    stream = storage.opened[-1]
    assert stream.closed and stream.chunks_read == 1


@pytest.mark.asyncio
async def test_whole_file_reads_are_not_used(client, world, storage, monkeypatch):
    async def get(key):
        raise AssertionError("evidence must be streamed, not read whole")

    monkeypatch.setattr(storage_service, "get", get)
    await as_user(client, ADMIN)
    assert (await client.get(content_url("admin", world["pdf"]))).content == PDF


# --------------------------------------------------------------------------- #
# Storage backends
# --------------------------------------------------------------------------- #
@pytest.fixture
def local_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "local_storage_path", str(tmp_path))
    local = LocalStorageService()
    for name in ("upload", "get", "open", "delete"):
        monkeypatch.setattr(storage_service, name, getattr(local, name))
    return local


@pytest.mark.asyncio
async def test_local_storage_is_delivered_through_the_api(client, seeded, local_storage, tmp_path):
    await as_user(client, ALC_A)
    activity = (await client.post("/api/portal/activities", json=ACTIVITY)).json()["id"]
    evidence_id = await upload(client, activity, "local.pdf", PDF, "application/pdf")
    access = await client.get(f"/api/portal/evidence/{evidence_id}/access")
    assert access.json() == {"url": content_url("portal", evidence_id)}
    response = await client.get(access.json()["url"])
    assert response.status_code == 200 and response.content == PDF
    assert response.headers["content-length"] == str(len(PDF))
    assert str(tmp_path) not in response.text + str(response.headers)


@pytest.mark.asyncio
async def test_local_stream_reads_in_chunks_and_closes(local_storage):
    body = b"x" * 200_000
    await local_storage.upload("evidence/a/b/c.pdf", body, "application/pdf")
    stream = await local_storage.open("evidence/a/b/c.pdf")
    assert stream.size == len(body)
    chunks = [chunk async for chunk in stream.chunks()]
    assert b"".join(chunks) == body and len(chunks) == 4  # 64 KiB chunks
    stream.close()
    stream.close()  # idempotent
    assert stream._handle.closed


@pytest.fixture
def windows_open(monkeypatch):
    """Make opening a directory fail the way it does on Windows (PermissionError rather than
    POSIX IsADirectoryError), so the directory checks below mean the same on every platform."""
    real_open = Path.open

    def open_(self, *args, **kwargs):
        if self.is_dir():
            raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_)


@pytest.mark.asyncio
async def test_local_missing_or_escaping_keys_are_not_found(local_storage, windows_open):
    await local_storage.upload("evidence/a/b/c.pdf", PDF, "application/pdf")
    # Missing, outside the storage root, the storage root itself, and a directory inside it.
    for key in ("evidence/missing.pdf", "../outside.pdf", ".", "evidence", "evidence/a/b"):
        with pytest.raises(StorageObjectNotFound):
            await local_storage.open(key)
    stream = await local_storage.open("evidence/a/b/c.pdf")  # a regular file still opens
    stream.close()


@pytest.mark.asyncio
async def test_local_unreadable_file_is_an_error_not_missing(
    client, seeded, local_storage, monkeypatch
):
    await as_user(client, ALC_A)
    activity = (await client.post("/api/portal/activities", json=ACTIVITY)).json()["id"]
    evidence_id = await upload(client, activity, "locked.pdf", PDF, "application/pdf")

    def denied(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "open", denied)
    key = next(local_storage.root.rglob("*.pdf")).relative_to(local_storage.root).as_posix()
    with pytest.raises(PermissionError):
        await local_storage.open(key)
    # Through the API it is a storage failure (503), never disguised as missing (404).
    response = await client.get(content_url("portal", evidence_id))
    assert response.status_code == 503
    assert str(local_storage.root) not in response.text


@pytest.mark.asyncio
async def test_local_file_removed_after_the_check_is_not_found(local_storage, monkeypatch):
    # The file passes the regular-file check, then disappears before it is opened.
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    with pytest.raises(StorageObjectNotFound):
        await local_storage.open("evidence/raced.pdf")


class _Body:
    def __init__(self, data):
        self.data, self.offset, self.close_calls, self.reads = data, 0, 0, []

    def read(self, size):
        self.reads.append(size)
        chunk = self.data[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self):
        self.close_calls += 1


class _S3Client:
    def __init__(self, body=None, error=None):
        self.body, self.error, self.calls = body, error, []

    def get_object(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"Body": self.body, "ContentLength": len(self.body.data)}


def _s3(client):
    service = S3StorageService.__new__(S3StorageService)
    service.client = client
    return service


@pytest.mark.asyncio
async def test_s3_stream_reads_in_chunks_server_side_and_closes():
    body = _Body(b"y" * 150_000)
    client = _S3Client(body)
    stream = await _s3(client).open("evidence/a/b/c.pdf")
    assert client.calls == [{"Bucket": settings.s3_bucket, "Key": "evidence/a/b/c.pdf"}]
    assert stream.size == 150_000
    chunks = [chunk async for chunk in stream.chunks()]
    assert b"".join(chunks) == body.data and len(chunks) == 3
    assert set(body.reads) == {64 * 1024}  # never one whole-object read
    stream.close()
    assert body.close_calls == 1


@pytest.mark.asyncio
async def test_s3_missing_object_and_other_errors():
    missing = ClientError({"Error": {"Code": "NoSuchKey", "Message": "x"}}, "GetObject")
    with pytest.raises(StorageObjectNotFound):
        await _s3(_S3Client(error=missing)).open("evidence/missing.pdf")
    denied = ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "GetObject")
    with pytest.raises(ClientError):
        await _s3(_S3Client(error=denied)).open("evidence/denied.pdf")


@pytest.mark.asyncio
async def test_s3_access_denied_becomes_a_503(client, world, storage):
    storage.open_error = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied", "BucketName": "alc-evidence"}},
        "GetObject",
    )
    await as_user(client, ADMIN)
    response = await client.get(content_url("admin", world["pdf"]))
    assert response.status_code == 503
    assert_no_storage_details(response, storage)


# --------------------------------------------------------------------------- #
# Header helpers
# --------------------------------------------------------------------------- #
def test_previewable_types_are_exactly_the_accepted_upload_types():
    assert set(PREVIEWABLE_TYPES) == set(ALLOWED_MIMES)


@pytest.mark.parametrize(
    ("mime", "expected"),
    [("application/pdf", "application/pdf"), ("image/webp", "image/webp"),
     ("text/html", "application/octet-stream"), ("image/svg+xml", "application/octet-stream"),
     ("application/pdf\r\nX-Evil: 1", "application/octet-stream"),
     (None, "application/octet-stream")],
)
def test_content_type_uses_only_known_stored_types(mime, expected):
    assert content_type(mime) == expected


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("proof.pdf", "proof.pdf"),
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("C:\\Users\\me\\scan.png", "scan.png"),
        ("evil\r\nSet-Cookie: a=b.pdf", "evilSet-Cookie: a=b.pdf"),
        ("", "evidence.pdf"),
        (None, "evidence.pdf"),
        (" .. ", "evidence.pdf"),
        ("a" * 300 + ".pdf", "a" * 150),
    ],
)
def test_safe_filename(original, expected):
    assert safe_filename(original, "application/pdf") == expected


def test_content_disposition_is_a_single_safe_header_value():
    value = content_disposition('रिपोर्ट "final"\r\n;x=y.pdf', "application/pdf")
    assert "\r" not in value and "\n" not in value
    plain, encoded = value.split("; filename*=UTF-8''")
    assert plain == 'inline; filename="final_x_y.pdf"'
    assert encoded.isascii() and '"' not in encoded and ";" not in encoded and " " not in encoded
    assert content_disposition("x.bin", "application/octet-stream").startswith("attachment;")
    assert content_disposition("नाव", "image/png") == (
        "inline; filename=\"evidence\"; filename*=UTF-8''%E0%A4%A8%E0%A4%BE%E0%A4%B5"
    )


def test_stream_helper_is_used_for_every_evidence_route():
    # Both content routes go through the same delivery helper (one implementation).
    from app.routes import admin, portal

    assert portal.evidence_response is evidence_delivery.evidence_response
    assert admin.evidence_response is evidence_delivery.evidence_response
    assert isinstance(MemoryObjectStream(b"", 1), evidence_delivery.ObjectStream)
