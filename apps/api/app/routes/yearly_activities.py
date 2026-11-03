"""DCUs write only their runs; all roles get server-scoped read-only views."""
import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import pagination, require_csrf, require_dcu, require_operational_user
from app.models import User
from app.services import yearly_activities as service
from app.yearly_schemas import CompletionIn, DefinitionIn, RunIn

router = APIRouter(prefix="/yearly-activities", tags=["Yearly Activities"],
                   dependencies=[Depends(require_csrf)])


@router.get("/definitions")
async def definitions(
    year: int | None = Query(default=None, ge=2000, le=2100),
    q: str | None = Query(default=None, max_length=180),
    paging: tuple[int, int] = Depends(pagination),
    user: User = Depends(require_operational_user), db: AsyncSession = Depends(get_db),
):
    return await service.list_definitions(db, user, year=year or service.current_year(), q=q,
                                           page=paging[0], page_size=paging[1])


@router.post("/definitions", status_code=201)
async def create_definition(data: DefinitionIn, request: Request,
                            user: User = Depends(require_dcu),
                            db: AsyncSession = Depends(get_db)):
    return await service.create_definition(db, user, data, request)


@router.patch("/runs/{run_id}")
async def update_run(run_id: uuid.UUID, data: RunIn, request: Request,
                     user: User = Depends(require_dcu), db: AsyncSession = Depends(get_db)):
    return await service.update_run(db, user, run_id, data, request)


@router.get("/runs/{run_id}/completions")
async def completions(
    run_id: uuid.UUID, q: str | None = Query(default=None, max_length=180),
    sbu_id: uuid.UUID | None = None, paging: tuple[int, int] = Depends(pagination),
    user: User = Depends(require_operational_user), db: AsyncSession = Depends(get_db),
):
    return await service.list_completions(db, user, run_id, q=q, sbu_id=sbu_id,
                                         page=paging[0], page_size=paging[1])


@router.patch("/runs/{run_id}/completions/{alc_id}")
async def mark_completion(run_id: uuid.UUID, alc_id: uuid.UUID, data: CompletionIn,
                          request: Request, user: User = Depends(require_dcu),
                          db: AsyncSession = Depends(get_db)):
    return await service.mark_completion(db, user, run_id, alc_id, data, request)


@router.post("/runs/{run_id}/finalize")
async def finalize_run(run_id: uuid.UUID, request: Request,
                       user: User = Depends(require_dcu), db: AsyncSession = Depends(get_db)):
    return await service.finalize_run(db, user, run_id, request)
