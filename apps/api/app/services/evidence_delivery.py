"""Evidence file delivery through the authenticated API.

Evidence stays in private object storage (S3/MinIO, or the local development store). The
browser never receives a storage URL, bucket, object key or filesystem path: it requests
``GET /api/portal/evidence/{id}/content`` (DCU / SBU / ALC) or
``GET /api/admin/evidence/{id}/content`` (ADMIN) with its normal session cookies, the route
authorizes the user against the evidence's activity, and only then is the object opened
server-side and streamed back in chunks. The object store therefore only has to be reachable
from the API server (for example ``127.0.0.1:9000``), never from users' browsers.

This module does no authorization of its own: callers pass evidence they have already scoped.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from urllib.parse import quote

import structlog
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.models import ActivityEvidence
from app.storage import storage_service
from app.storage.base import ObjectStream, StorageObjectNotFound

logger = structlog.get_logger()

# The evidence types uploads accept (their type is detected from the file's own bytes at
# upload, never taken from the client) and that browsers can display inline.
PREVIEWABLE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}
FALLBACK_TYPE = "application/octet-stream"
MAX_FILENAME_LENGTH = 150
_ASCII_UNSAFE = re.compile(r"[^A-Za-z0-9._()\- ]+")

NOT_FOUND = "Evidence not found"
UNAVAILABLE = "Evidence is temporarily unavailable"


def content_type(mime_type: str | None) -> str:
    """The stored (upload-detected) type when it is one we serve, otherwise a safe fallback."""
    return mime_type if mime_type in PREVIEWABLE_TYPES else FALLBACK_TYPE


def safe_filename(original: str | None, media_type: str) -> str:
    """The uploaded file's own name: last path segment only, no control characters (so no
    CR/LF header injection), trimmed; ``evidence<ext>`` when nothing usable is left."""
    name = (original or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip(" .")
    return name[:MAX_FILENAME_LENGTH] or f"evidence{PREVIEWABLE_TYPES.get(media_type, '')}"


def content_disposition(original: str | None, media_type: str) -> str:
    """``inline`` for previewable evidence, ``attachment`` for anything else, with an ASCII
    ``filename`` and the exact name as RFC 6266 / 5987 ``filename*``."""
    name = safe_filename(original, media_type)
    fallback = _ASCII_UNSAFE.sub("_", name).strip(" ._") or "evidence"
    disposition = "inline" if media_type in PREVIEWABLE_TYPES else "attachment"
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


async def stream_chunks(stream: ObjectStream, evidence_id: str) -> AsyncIterator[bytes]:
    """Yield the object's chunks, closing it as soon as the last one is read or reading fails."""
    try:
        async for chunk in stream.chunks():
            yield chunk
    except Exception as exc:
        # The status line is already sent, so the server just ends the connection early.
        logger.warning("evidence_stream_failed", evidence_id=evidence_id,
                       error_type=type(exc).__name__)
        raise
    finally:
        stream.close()


class EvidenceStreamingResponse(StreamingResponse):
    """Closes the storage object however the response ends — completed, failed, or cut short
    by a client disconnect (even before the first chunk was read)."""

    def __init__(self, stream: ObjectStream, evidence_id: str, **kwargs) -> None:
        super().__init__(stream_chunks(stream, evidence_id), **kwargs)
        self.storage_stream = stream

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.storage_stream.close()


async def evidence_response(evidence: ActivityEvidence) -> EvidenceStreamingResponse:
    """Stream already-authorized evidence from private storage.

    A missing object is a 404, like evidence that does not exist; any other storage failure is
    a 503. Neither response, nor the log entry, carries the bucket, key, path or storage URL.
    """
    evidence_id = str(evidence.id)
    try:
        stream = await storage_service.open(evidence.storage_key)
    except StorageObjectNotFound:
        logger.warning("evidence_object_missing", evidence_id=evidence_id)
        raise HTTPException(status_code=404, detail=NOT_FOUND) from None
    except Exception as exc:
        logger.warning("evidence_storage_unavailable", evidence_id=evidence_id,
                       error_type=type(exc).__name__)
        raise HTTPException(status_code=503, detail=UNAVAILABLE) from None
    media_type = content_type(evidence.mime_type)
    headers = {
        "Content-Disposition": content_disposition(evidence.original_filename, media_type),
        # Evidence may be sensitive: never stored by the browser or any shared cache.
        "Cache-Control": "private, no-store",
    }
    if stream.size is not None:
        headers["Content-Length"] = str(stream.size)
    return EvidenceStreamingResponse(stream, evidence_id, media_type=media_type, headers=headers)


def content_path_for(route_name: str, evidence_id: uuid.UUID, app) -> str:
    """Root-relative application URL of an evidence content route (e.g.
    ``/api/portal/evidence/<id>/content``) — never a storage URL."""
    return str(app.url_path_for(route_name, evidence_id=str(evidence_id)))
