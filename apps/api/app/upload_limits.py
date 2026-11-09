"""Limit yearly multipart bytes before spooling, including chunked requests."""
import asyncio
import time

from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse

from app.config import settings

REQUEST_BODY_TIMEOUT = 60


class YearlyUploadLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (scope["type"] != "http" or scope.get("method") != "POST"
                or "/yearly-activities/submissions/" not in path or not path.endswith("/evidence")):
            return await self.app(scope, receive, send)
        limit = settings.max_upload_bytes + 128 * 1024
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Invalid content length"}, status_code=400)(
                scope, receive, send)
        if declared > limit:
            return await JSONResponse({"detail": "Evidence request too large"}, status_code=413)(
                scope, receive, send)
        received = 0
        deadline = time.monotonic() + REQUEST_BODY_TIMEOUT
        failure_status = None

        async def bounded_receive():
            nonlocal received, failure_status
            try:
                message = await asyncio.wait_for(receive(), max(0, deadline-time.monotonic()))
            except TimeoutError as exc:
                failure_status = 408
                raise MultiPartException("Evidence request body timed out") from exc
            received += len(message.get("body", b""))
            if received > limit:
                failure_status = 413
                raise MultiPartException("Evidence request too large")
            return message

        async def bounded_send(message):
            # MultiPartException closes parser spool files. Starlette renders its detail
            # as HTTP 400; preserve the more precise boundary status for the client.
            if message["type"] == "http.response.start" and failure_status is not None:
                message = {**message, "status": failure_status}
            await send(message)

        return await self.app(scope, bounded_receive, bounded_send)
