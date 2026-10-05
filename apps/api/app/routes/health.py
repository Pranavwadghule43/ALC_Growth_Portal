"""Operational health endpoints (Phase 5B-3). Public, unauthenticated, deliberately minimal.

* ``GET /api/health/live``  - liveness: the process is up and answering requests. It touches
  no dependency, so a database, Redis or storage outage never makes it fail.
* ``GET /api/health/ready`` - readiness: the dependencies needed for normal traffic are
  usable. 200 when all are, 503 when any is not.
* ``GET /api/health``       - the original endpoint, kept as an alias of liveness.

Responses carry fixed words only (``ok`` / ``error`` / ``ready`` / ``not_ready``): no
environment, version, host, URL, bucket, path, credential or exception text.
"""
from fastapi import APIRouter, Response

from app.services import health

router = APIRouter(prefix="/health", tags=["Health"])
NO_STORE = "no-store"


@router.get("")
@router.get("/live")
async def live(response: Response):
    response.headers["Cache-Control"] = NO_STORE
    return {"status": "ok"}


@router.get("/ready")
async def ready(response: Response):
    response.headers["Cache-Control"] = NO_STORE
    is_ready, checks = await health.readiness()
    if not is_ready:
        response.status_code = 503
    return {"status": "ready" if is_ready else "not_ready", "checks": checks}
