"""DCUs write only their runs; all roles get server-scoped read-only views."""

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, release_read_only_transaction
from app.dependencies import (
    pagination,
    require_alc,
    require_csrf,
    require_dcu,
    require_operational_user,
)
from app.models import User
from app.response_cache import get_or_create
from app.services import yearly_activities as service
from app.services import yearly_submissions as submissions
from app.services.evidence_delivery import evidence_response
from app.yearly_schemas import CompletionIn, DefinitionIn, RunIn, SubmissionIn, YearlyReviewIn

router = APIRouter(
    prefix="/yearly-activities", tags=["Yearly Activities"], dependencies=[Depends(require_csrf)]
)


@router.get("/definitions")
async def definitions(
    year: int | None = Query(default=None, ge=2000, le=2100),
    q: str | None = Query(default=None, max_length=180),
    paging: tuple[int, int] = Depends(pagination),
    user: User = Depends(require_operational_user),
    db: AsyncSession = Depends(get_db),
):
    await release_read_only_transaction(db)
    selected_year = year or service.current_year()

    async def build():
        payload = await service.list_definitions(
            db, user, year=selected_year, q=q, page=paging[0], page_size=paging[1]
        )
        await release_read_only_transaction(db)
        return payload

    # Cache the bounded common page only. Arbitrary searches/pagination bypass Redis so a
    # user cannot create unbounded key cardinality.
    return (
        await get_or_create(
            "yearly-definitions",
            (str(user.id), user.role.value, selected_year, paging[0], paging[1]),
            settings.response_cache_definitions_ttl_seconds,
            build,
            cacheable=not q and paging == (1, 25),
        )
    ).value


@router.post("/definitions", status_code=201)
async def create_definition(
    data: DefinitionIn,
    request: Request,
    user: User = Depends(require_dcu),
    db: AsyncSession = Depends(get_db),
):
    return await service.create_definition(db, user, data, request)


@router.patch("/runs/{run_id}")
async def update_run(
    run_id: uuid.UUID,
    data: RunIn,
    request: Request,
    user: User = Depends(require_dcu),
    db: AsyncSession = Depends(get_db),
):
    return await service.update_run(db, user, run_id, data, request)


@router.get("/runs/{run_id}/completions")
async def completions(
    run_id: uuid.UUID,
    q: str | None = Query(default=None, max_length=180),
    sbu_id: uuid.UUID | None = None,
    paging: tuple[int, int] = Depends(pagination),
    user: User = Depends(require_operational_user),
    db: AsyncSession = Depends(get_db),
):
    payload = await service.list_completions(
        db, user, run_id, q=q, sbu_id=sbu_id, page=paging[0], page_size=paging[1]
    )
    await release_read_only_transaction(db)
    return payload


@router.patch("/runs/{run_id}/completions/{alc_id}")
async def mark_completion(
    run_id: uuid.UUID,
    alc_id: uuid.UUID,
    data: CompletionIn,
    request: Request,
    user: User = Depends(require_dcu),
    db: AsyncSession = Depends(get_db),
):
    raise HTTPException(410, "Manual completion is retired; review an ALC evidence submission")


@router.post("/runs/{run_id}/finalize")
async def finalize_run(
    run_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_dcu),
    db: AsyncSession = Depends(get_db),
):
    return await service.finalize_run(db, user, run_id, request)


@router.get("/submissions")
async def list_submissions(
    year: int | None = Query(None, ge=2000, le=2100),
    run_id: uuid.UUID | None = None,
    activity_id: uuid.UUID | None = None,
    sbu_id: uuid.UUID | None = None,
    status: str | None = Query(None, max_length=24),
    q: str | None = Query(None, max_length=180),
    paging: tuple[int, int] = Depends(pagination),
    user: User = Depends(require_operational_user),
    db: AsyncSession = Depends(get_db),
):
    payload = await submissions.listing(
        db,
        user,
        year=year or service.current_year(),
        run_id=run_id,
        activity_id=activity_id,
        sbu_id=sbu_id,
        status=status,
        q=q,
        page=paging[0],
        page_size=paging[1],
    )
    await release_read_only_transaction(db)
    return payload


@router.post("/runs/{run_id}/submission", status_code=201)
async def create_submission(
    run_id: uuid.UUID,
    data: SubmissionIn,
    request: Request,
    user: User = Depends(require_alc),
    db: AsyncSession = Depends(get_db),
):
    return await submissions.create(db, user, run_id, data, request)


@router.get("/submissions/{submission_id}")
async def submission_detail(
    submission_id: uuid.UUID,
    user: User = Depends(require_operational_user),
    db: AsyncSession = Depends(get_db),
):
    payload = await submissions.detail(db, user, submission_id)
    await release_read_only_transaction(db)
    return payload


@router.patch("/submissions/{submission_id}")
async def edit_submission(
    submission_id: uuid.UUID,
    data: SubmissionIn,
    request: Request,
    user: User = Depends(require_alc),
    db: AsyncSession = Depends(get_db),
):
    return await submissions.save(db, user, submission_id, data, request)


@router.post("/submissions/{submission_id}/submit")
async def submit(
    submission_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_alc),
    db: AsyncSession = Depends(get_db),
):
    return await submissions.submit(db, user, submission_id, request)


@router.post("/submissions/{submission_id}/review")
async def review(
    submission_id: uuid.UUID,
    data: YearlyReviewIn,
    request: Request,
    user: User = Depends(require_dcu),
    db: AsyncSession = Depends(get_db),
):
    return await submissions.review(db, user, submission_id, data, request)


@router.post("/submissions/{submission_id}/evidence", status_code=201)
async def upload(
    submission_id: uuid.UUID,
    request: Request,
    files: list[UploadFile] = File(...),
    user: User = Depends(require_alc),
    db: AsyncSession = Depends(get_db),
):
    return await submissions.upload(db, user, submission_id, files, request)


@router.delete("/evidence/{evidence_id}")
async def remove(
    evidence_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_alc),
    db: AsyncSession = Depends(get_db),
):
    return await submissions.remove(db, user, evidence_id, request)


@router.get("/evidence/{evidence_id}/access")
async def access(
    evidence_id: uuid.UUID,
    user: User = Depends(require_operational_user),
    db: AsyncSession = Depends(get_db),
):
    await submissions.scoped_evidence(db, user, evidence_id)
    await release_read_only_transaction(db)
    return {"url": f"/api/yearly-activities/evidence/{evidence_id}/content"}


@router.get("/evidence/{evidence_id}/content")
async def content(
    evidence_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_operational_user),
    db: AsyncSession = Depends(get_db),
):
    evidence = await submissions.scoped_evidence(db, user, evidence_id)
    # The evidence row's scalar metadata is fully loaded by scoped_evidence. Close the
    # auth/scope read transaction before opening or streaming the object from storage.
    await release_read_only_transaction(db)
    return await evidence_response(evidence, range_header=request.headers.get("range"))
