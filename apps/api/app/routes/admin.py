import csv
import io
import math
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, case, delete, desc, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import hash_password
from app.config import settings
from app.database import get_db
from app.dependencies import (
    USER_RESPONSE_LOADERS,
    load_user_for_response,
    pagination,
    require_admin,
    require_csrf,
)
from app.enums import ActivityStatus, ReviewAction, Role
from app.models import (
    ALC,
    DCU,
    RCU,
    SBU,
    Activity,
    ActivityEvidence,
    ActivityReview,
    ActivityRevision,
    AuditLog,
    Notification,
    Partner,
    RefreshToken,
    User,
)
from app.schemas import (
    ActivityOut,
    AlcStatusPatch,
    DcuOut,
    DecisionChangeIn,
    ReviewDecisionIn,
    SbuIn,
    SbuOut,
    SbuPatch,
    UserCreate,
    UserOut,
    UserPatch,
)
from app.services import alc_import
from app.services.activities import FINAL_STATUSES, admin_activity, review_activity
from app.services.audit import record_audit
from app.services.csv_export import safe_csv
from app.services.rollups import alc_activity_join
from app.services.scope import submitted_workflow
from app.services.sessions import revoke_user_sessions
from app.storage import storage_service

router = APIRouter(prefix="/admin", tags=["Admin"], dependencies=[Depends(require_csrf)])


@router.get("/dashboard")
async def dashboard(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    alc_counts = (
        (
            await db.execute(
                select(
                    func.count(ALC.id).label("total_alcs"),
                    func.count(case((ALC.status == "ACTIVE", 1))).label("active_alcs"),
                )
            )
        )
        .mappings()
        .one()
    )
    metrics = (
        (
            await db.execute(
                select(
                    func.count(case((Activity.status != ActivityStatus.DRAFT, 1))).label(
                        "activities_submitted"
                    ),
                    func.count(
                        case(
                            (
                                Activity.status.in_(
                                    [
                                        ActivityStatus.SUBMITTED,
                                        ActivityStatus.RESUBMITTED,
                                        ActivityStatus.UNDER_REVIEW,
                                    ]
                                ),
                                1,
                            )
                        )
                    ).label("pending_verification"),
                    func.count(case((Activity.status == ActivityStatus.VERIFIED, 1))).label(
                        "verified_activities"
                    ),
                    func.count(
                        case((Activity.status == ActivityStatus.CORRECTION_REQUIRED, 1))
                    ).label("correction_required"),
                    func.count(case((Activity.status == ActivityStatus.REJECTED, 1))).label(
                        "rejected_activities"
                    ),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    Activity.status == ActivityStatus.VERIFIED,
                                    Activity.learners_reached,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("verified_learners"),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    Activity.status == ActivityStatus.VERIFIED,
                                    Activity.leads_generated,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("verified_leads"),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    Activity.status == ActivityStatus.VERIFIED,
                                    Activity.admissions_generated,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("verified_admissions"),
                )
            )
        )
        .mappings()
        .one()
    )
    active_partners = (
        await db.scalar(select(func.count(Partner.id)).where(Partner.status == "ACTIVE")) or 0
    )
    status_rows = (
        await db.execute(
            select(Activity.status, func.count(Activity.id))
            .where(submitted_workflow())
            .group_by(Activity.status)
        )
    ).all()
    trend_rows = (
        await db.execute(
            select(Activity.activity_date, func.count(Activity.id))
            .where(submitted_workflow())
            .group_by(Activity.activity_date)
            .order_by(Activity.activity_date.desc())
            .limit(30)
        )
    ).all()
    categories = (
        await db.execute(
            select(Activity.activity_type, func.count(Activity.id))
            .where(Activity.status == ActivityStatus.VERIFIED)
            .group_by(Activity.activity_type)
            .order_by(func.count(Activity.id).desc())
            .limit(8)
        )
    ).all()
    return {
        **alc_counts,
        **metrics,
        "active_partnerships": active_partners,
        "status_distribution": [{"name": s.value, "value": c} for s, c in status_rows],
        "submission_trend": [{"date": str(d), "count": c} for d, c in reversed(trend_rows)],
        "verified_categories": [{"name": n, "value": c} for n, c in categories],
    }


def queue_filters(status, activity_type, ecosystem, alc_id, date_from, date_to, search):
    filters = [submitted_workflow()]
    if status:
        filters.append(Activity.status == status)
    if activity_type:
        filters.append(Activity.activity_type == activity_type)
    if ecosystem:
        filters.append(Activity.ecosystem == ecosystem)
    if alc_id:
        filters.append(Activity.alc_id == alc_id)
    if date_from:
        filters.append(Activity.submitted_at >= date_from)
    if date_to:
        filters.append(Activity.submitted_at < date_to)
    if search:
        filters.append(
            or_(
                Activity.activity_number.ilike(f"%{search}%"),
                ALC.alc_code.ilike(f"%{search}%"),
                ALC.alc_name.ilike(f"%{search}%"),
                Partner.partner_name.ilike(f"%{search}%"),
            )
        )
    return filters


@router.get("/verification-queue")
@router.get("/activities")
async def activities(
    page_data: tuple[int, int] = Depends(pagination),
    status: ActivityStatus | None = None,
    activity_type: str | None = None,
    ecosystem: str | None = None,
    alc_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
    queue_only: bool = False,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    page, page_size = page_data
    if queue_only and not status:
        status_values = [
            ActivityStatus.SUBMITTED,
            ActivityStatus.RESUBMITTED,
            ActivityStatus.UNDER_REVIEW,
        ]
        base = [Activity.status.in_(status_values)]
    else:
        base = []
    filters = base + queue_filters(
        status, activity_type, ecosystem, alc_id, date_from, date_to, search
    )
    joined = (
        select(Activity, ALC)
        .join(ALC, Activity.alc_id == ALC.id)
        .outerjoin(Partner, Activity.partner_id == Partner.id)
        .where(*filters)
    )
    total = (
        await db.scalar(
            select(func.count(Activity.id))
            .join(ALC, Activity.alc_id == ALC.id)
            .outerjoin(Partner, Activity.partner_id == Partner.id)
            .where(*filters)
        )
        or 0
    )
    rows = (
        await db.execute(
            joined.options(
                selectinload(Activity.partner),
                selectinload(Activity.evidence),
                selectinload(Activity.reviews),
                selectinload(Activity.revisions),
            )
            .order_by(desc(Activity.submitted_at), desc(Activity.updated_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": [
            {
                "activity": ActivityOut.model_validate(activity),
                "alc": {
                    "id": alc.id,
                    "alc_code": alc.alc_code,
                    "alc_name": alc.alc_name,
                    "status": alc.status,
                },
            }
            for activity, alc in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": math.ceil(total / page_size) if total else 0,
    }


@router.get("/activities/{activity_id}")
async def get_activity(
    activity_id: uuid.UUID, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    activity = await admin_activity(db, activity_id)
    alc = await db.scalar(
        select(ALC)
        .options(selectinload(ALC.sbu).selectinload(SBU.dcu))
        .where(ALC.id == activity.alc_id)
    )
    return {
        "activity": ActivityOut.model_validate(activity),
        "alc": {
            "id": alc.id,
            "alc_code": alc.alc_code,
            "alc_name": alc.alc_name,
            "status": alc.status,
        },
        # Admin is global; surface the ALC's assigned SBU for context (may be unassigned).
        "sbu": (
            {"id": alc.sbu.id, "code": alc.sbu.code, "name": alc.sbu.name}
            if alc.sbu
            else None
        ),
        "dcu": (
            {"id": alc.sbu.dcu.id, "code": alc.sbu.dcu.code, "name": alc.sbu.dcu.name}
            if alc.sbu and alc.sbu.dcu
            else None
        ),
        "can_change_decision": activity.status in FINAL_STATUSES,
    }


async def make_decision(
    activity_id: uuid.UUID,
    payload: ReviewDecisionIn,
    action: ReviewAction,
    request: Request,
    admin: User,
    db: AsyncSession,
):
    activity = await admin_activity(db, activity_id, lock=True)
    await review_activity(db, activity, admin, action, payload.remark)
    await record_audit(
        db,
        action.value.lower(),
        "activity",
        activity.id,
        admin,
        request,
        {"remark": payload.remark},
    )
    await db.commit()
    return await admin_activity(db, activity.id)


@router.post("/activities/{activity_id}/verify", response_model=ActivityOut)
async def verify(
    activity_id: uuid.UUID,
    payload: ReviewDecisionIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await make_decision(activity_id, payload, ReviewAction.VERIFY, request, admin, db)


@router.post("/activities/{activity_id}/request-correction", response_model=ActivityOut)
async def request_correction(
    activity_id: uuid.UUID,
    payload: ReviewDecisionIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await make_decision(
        activity_id, payload, ReviewAction.REQUEST_CORRECTION, request, admin, db
    )


@router.post("/activities/{activity_id}/reject", response_model=ActivityOut)
async def reject(
    activity_id: uuid.UUID,
    payload: ReviewDecisionIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await make_decision(activity_id, payload, ReviewAction.REJECT, request, admin, db)


@router.post("/activities/{activity_id}/change-decision", response_model=ActivityOut)
async def change_decision(
    activity_id: uuid.UUID,
    payload: DecisionChangeIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin override of a final decision (VERIFIED / REJECTED). Reason required for every
    target decision; appends a decision-change review, never rewrites history."""
    activity = await admin_activity(db, activity_id, lock=True)
    previous = activity.status
    await review_activity(
        db, activity, admin, payload.decision, payload.remark, change_decision=True
    )
    await record_audit(
        db,
        "decision_changed",
        "activity",
        activity.id,
        admin,
        request,
        {
            "previous_status": previous.value,
            "new_status": activity.status.value,
            "remark": payload.remark,
            "alc_id": str(activity.alc_id),
        },
    )
    await db.commit()
    db.expire(activity, ["reviews", "revisions"])
    return await admin_activity(db, activity.id)


@router.get("/evidence/{evidence_id}/access")
async def evidence_access(
    evidence_id: uuid.UUID,
    request: Request,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    evidence = await db.scalar(
        select(ActivityEvidence)
        .join(Activity)
        .where(
            ActivityEvidence.id == evidence_id,
            ActivityEvidence.is_active.is_(True),
            submitted_workflow(),
        )
    )
    if not evidence:
        raise HTTPException(status_code=404, detail="Evidence not found")
    if settings.storage_backend == "local":
        return {"url": f"{str(request.base_url).rstrip('/')}/api/admin/evidence/{evidence_id}/content", "expires_in": 0}
    return {"url": await storage_service.get_secure_url(evidence.storage_key), "expires_in": settings.s3_presign_seconds}


@router.get("/evidence/{evidence_id}/content")
async def evidence_content(
    evidence_id: uuid.UUID,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if settings.storage_backend != "local":
        raise HTTPException(status_code=404, detail="Evidence not found")
    evidence = await db.scalar(
        select(ActivityEvidence)
        .join(Activity)
        .where(
            ActivityEvidence.id == evidence_id,
            ActivityEvidence.is_active.is_(True),
            submitted_workflow(),
        )
    )
    if not evidence:
        raise HTTPException(status_code=404, detail="Evidence not found")
    try:
        content = await storage_service.get(evidence.storage_key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Evidence not found") from None
    return Response(content, media_type=evidence.mime_type, headers={"Cache-Control": "private, no-store"})


@router.get("/alcs")
async def alcs(
    page_data: tuple[int, int] = Depends(pagination),
    search: str | None = None,
    status: str | None = None,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    page, page_size = page_data
    filters = []
    if search:
        filters.append(or_(ALC.alc_code.ilike(f"%{search}%"), ALC.alc_name.ilike(f"%{search}%")))
    if status:
        filters.append(ALC.status == status)
    total = await db.scalar(select(func.count(ALC.id)).where(*filters)) or 0
    rows = (
        (
            await db.execute(
                select(
                    ALC.id,
                    ALC.alc_code,
                    ALC.alc_name,
                    ALC.status,
                    func.count(Activity.id).label("activities"),
                    func.count(case((Activity.status == ActivityStatus.VERIFIED, 1))).label(
                        "verified"
                    ),
                    func.count(
                        case(
                            (
                                Activity.status.in_(
                                    [
                                        ActivityStatus.SUBMITTED,
                                        ActivityStatus.RESUBMITTED,
                                        ActivityStatus.UNDER_REVIEW,
                                    ]
                                ),
                                1,
                            )
                        )
                    ).label("pending"),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    Activity.status == ActivityStatus.VERIFIED,
                                    Activity.learners_reached,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("learners"),
                    func.max(Activity.activity_date).label("last_activity"),
                )
                .outerjoin(Activity, alc_activity_join())
                .where(*filters)
                .group_by(ALC.id)
                .order_by(ALC.alc_code)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": rows,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": math.ceil(total / page_size) if total else 0,
    }


@router.get("/alcs/{alc_id}")
async def alc_detail(
    alc_id: uuid.UUID, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    alc = await db.get(ALC, alc_id)
    if not alc:
        raise HTTPException(status_code=404, detail="ALC not found")
    activities = (
        await db.scalars(
            select(Activity)
            .options(
                selectinload(Activity.partner),
                selectinload(Activity.evidence),
                selectinload(Activity.reviews),
                selectinload(Activity.revisions),
            )
            .where(Activity.alc_id == alc_id, submitted_workflow())
            .order_by(desc(Activity.updated_at))
            .limit(100)
        )
    ).all()
    partners = (
        await db.scalars(
            select(Partner).where(Partner.alc_id == alc_id).order_by(Partner.partner_name)
        )
    ).all()
    return {
        "alc": alc,
        "activities": [ActivityOut.model_validate(x) for x in activities],
        "partners": partners,
    }


@router.patch("/alcs/{alc_id}")
async def update_alc_status(
    alc_id: uuid.UUID,
    payload: AlcStatusPatch,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    alc = await db.get(ALC, alc_id)
    if not alc:
        raise HTTPException(status_code=404, detail="ALC not found")
    provided = payload.model_fields_set
    changes: dict = {}
    if "status" in provided and payload.status is not None:
        alc.status = payload.status
        changes["status"] = payload.status.value
    if "sbu_id" in provided:
        if payload.sbu_id is not None and not await db.scalar(
            select(SBU.id).where(SBU.id == payload.sbu_id)
        ):
            raise HTTPException(status_code=422, detail="Invalid SBU")
        # Reassignment moves only this pointer: the ALC keeps its id, users, activities,
        # partners, evidence, reviews and revisions. Scope follows on the next request.
        changes["previous_sbu_id"] = str(alc.sbu_id) if alc.sbu_id else None
        alc.sbu_id = payload.sbu_id
        changes["sbu_id"] = str(payload.sbu_id) if payload.sbu_id else None
    if not changes:
        raise HTTPException(status_code=422, detail="No changes supplied")
    await record_audit(db, "alc_updated", "alc", alc.id, admin, request, changes)
    await db.commit()
    return {
        "id": alc.id,
        "alc_code": alc.alc_code,
        "alc_name": alc.alc_name,
        "status": alc.status,
        "sbu_id": alc.sbu_id,
    }


@router.post("/alcs/import/validate")
async def validate_alc_import(
    file: UploadFile = File(...),
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Parse and classify a master file (ALC Code, ALC Name, SBU) without writing anything."""
    content = await file.read()
    try:
        records = alc_import.parse_source(file.filename, content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return await alc_import.validate(db, records)


@router.post("/alcs/import")
async def run_alc_import(
    request: Request,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Import the ALC master transactionally: create new ALCs and update the name/SBU of
    existing ones by ALC Code. Never creates login accounts, never touches passwords, and
    never deletes ALCs absent from the source. Blocked (nothing written) if any row is
    invalid or references an SBU that does not already exist."""
    content = await file.read()
    try:
        records = alc_import.parse_source(file.filename, content)
        summary = await alc_import.perform(db, records)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except alc_import.ImportBlocked as exc:
        raise HTTPException(status_code=422, detail=exc.detail) from None
    summary["by_sbu"] = await alc_import.counts_by_sbu(db)
    summary["not_in_source"] = await alc_import.orphan_codes(db, records)
    await record_audit(
        db,
        "alc_master_imported",
        "alc",
        None,
        admin,
        request,
        {
            "total": summary["total"],
            "created": summary["created"],
            "updated": summary["updated"],
            "unchanged": summary["unchanged"],
        },
    )
    await db.commit()
    return summary


@router.get("/partners")
async def all_partners(
    page_data: tuple[int, int] = Depends(pagination),
    search: str | None = None,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    page, page_size = page_data
    filters = []
    if search:
        filters.append(
            or_(
                Partner.partner_name.ilike(f"%{search}%"),
                ALC.alc_name.ilike(f"%{search}%"),
                ALC.alc_code.ilike(f"%{search}%"),
            )
        )
    total = await db.scalar(select(func.count(Partner.id)).join(ALC).where(*filters)) or 0
    rows = (
        await db.execute(
            select(Partner, ALC)
            .join(ALC)
            .where(*filters)
            .order_by(Partner.partner_name)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": [{"partner": partner, "alc": alc} for partner, alc in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": math.ceil(total / page_size) if total else 0,
    }


@router.get("/challenge")
async def challenge_progress(
    page_data: tuple[int, int] = Depends(pagination),
    search: str | None = None,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    page, page_size = page_data
    today = date.today()
    start = today - timedelta(days=29)
    filters = (
        [or_(ALC.alc_code.ilike(f"%{search}%"), ALC.alc_name.ilike(f"%{search}%"))]
        if search
        else []
    )
    total = await db.scalar(select(func.count(ALC.id)).where(*filters)) or 0
    rows = (
        (
            await db.execute(
                select(
                    ALC.id,
                    ALC.alc_code,
                    ALC.alc_name,
                    func.coalesce(func.sum(Activity.leads_generated), 0).label("prospects"),
                    func.count(
                        case(
                            (
                                func.lower(Activity.activity_type).like("%meeting%"),
                                1,
                            )
                        )
                    ).label("meetings"),
                    func.count(
                        case(
                            (
                                func.lower(Activity.activity_type).like("%pilot%"),
                                1,
                            )
                        )
                    ).label("pilots"),
                    func.count(
                        func.distinct(
                            case(
                                (
                                    func.lower(Activity.activity_type).like("%partnership%"),
                                    Activity.partner_id,
                                )
                            )
                        )
                    ).label("partnerships"),
                )
                .outerjoin(
                    Activity,
                    and_(
                        ALC.id == Activity.alc_id,
                        Activity.status == ActivityStatus.VERIFIED,
                        Activity.activity_date >= start,
                        Activity.activity_date <= today,
                    ),
                )
                .where(*filters)
                .group_by(ALC.id)
                .order_by(ALC.alc_code)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": rows,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": math.ceil(total / page_size) if total else 0,
        "targets": {"prospects": 40, "meetings": 20, "pilots": 10, "partnerships": 5},
    }


@router.get("/users", response_model=list[UserOut])
async def users(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return (
        await db.scalars(
            select(User).options(*USER_RESPONSE_LOADERS).order_by(User.username)
        )
    ).all()


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    payload: UserCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if payload.role == Role.ALC:
        if not payload.alc_id:
            raise HTTPException(status_code=422, detail="ALC is required")
        if payload.sbu_id or payload.dcu_id:
            raise HTTPException(status_code=422, detail="ALC user cannot belong to an SBU or DCU")
    elif payload.role == Role.SBU:
        if not payload.sbu_id:
            raise HTTPException(status_code=422, detail="SBU is required")
        if payload.alc_id or payload.dcu_id:
            raise HTTPException(status_code=422, detail="SBU user cannot belong to an ALC or DCU")
        if not await db.scalar(select(SBU.id).where(SBU.id == payload.sbu_id)):
            raise HTTPException(status_code=422, detail="Invalid SBU")
    elif payload.role == Role.DCU:
        # A DCU login is linked to exactly one DCU and nothing else.
        if not payload.dcu_id:
            raise HTTPException(status_code=422, detail="DCU is required")
        if payload.alc_id or payload.sbu_id:
            raise HTTPException(status_code=422, detail="DCU user cannot belong to an ALC or SBU")
        if not await db.scalar(select(DCU.id).where(DCU.id == payload.dcu_id)):
            raise HTTPException(status_code=422, detail="Invalid DCU")
    elif payload.role == Role.ADMIN and (payload.alc_id or payload.sbu_id or payload.dcu_id):
        raise HTTPException(
            status_code=422, detail="Admin cannot belong to an ALC, SBU or DCU"
        )
    if await db.scalar(select(User.id).where(User.username == payload.username)):
        raise HTTPException(status_code=409, detail="Username already exists")
    user = User(
        username=payload.username,
        email=str(payload.email) if payload.email else None,
        role=payload.role,
        alc_id=payload.alc_id if payload.role == Role.ALC else None,
        sbu_id=payload.sbu_id if payload.role == Role.SBU else None,
        dcu_id=payload.dcu_id if payload.role == Role.DCU else None,
        password_hash=hash_password(payload.password),
        must_change_password=payload.must_change_password,
    )
    db.add(user)
    await db.flush()
    await record_audit(db, "user_created", "user", user.id, admin, request)
    await db.commit()
    return await load_user_for_response(db, user.id)


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    payload: UserPatch,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.password:
        user.password_hash = hash_password(payload.password)
        user.must_change_password = (
            True if payload.must_change_password is None else payload.must_change_password
        )
        await revoke_user_sessions(db, user.id)
    elif payload.must_change_password is not None:
        user.must_change_password = payload.must_change_password
    changes = payload.model_dump(exclude_none=True, exclude={"password"})
    if payload.password:
        changes["password_reset"] = True
    await record_audit(db, "user_updated", "user", user.id, admin, request, changes)
    await db.commit()
    return await load_user_for_response(db, user.id)


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: uuid.UUID,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete an ALC/SBU/DCU login account only. The ALC/SBU master record and all
    historical activities, evidence, reviews, partners, tasks, challenge history and reports
    are preserved (authorship references are detached, not deleted). ADMIN accounts and the
    admin's own account can never be deleted."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == Role.ADMIN:
        raise HTTPException(status_code=403, detail="Administrator accounts cannot be deleted")
    if user.id == admin.id:
        raise HTTPException(status_code=403, detail="You cannot delete your own account")

    deleted_role, deleted_alc_id, deleted_sbu_id, deleted_dcu_id = (
        user.role,
        user.alc_id,
        user.sbu_id,
        user.dcu_id,
    )

    # Session cleanup: revoke every refresh token and drop notifications so the account
    # immediately loses access and an old refresh token can never be reused.
    await db.execute(delete(RefreshToken).where(RefreshToken.user_id == user_id))
    await db.execute(delete(Notification).where(Notification.user_id == user_id))

    # Detach authorship from historical rows so they survive (never cascade-delete business
    # data). Works on PostgreSQL and SQLite regardless of FK enforcement.
    await db.execute(
        update(Activity).where(Activity.created_by == user_id).values(created_by=None)
    )
    await db.execute(
        update(Activity).where(Activity.verified_by == user_id).values(verified_by=None)
    )
    await db.execute(
        update(ActivityEvidence)
        .where(ActivityEvidence.uploaded_by == user_id)
        .values(uploaded_by=None)
    )
    await db.execute(
        update(ActivityReview).where(ActivityReview.reviewer_id == user_id).values(reviewer_id=None)
    )
    await db.execute(
        update(ActivityRevision)
        .where(ActivityRevision.changed_by == user_id)
        .values(changed_by=None)
    )
    await db.execute(
        update(AuditLog).where(AuditLog.actor_user_id == user_id).values(actor_user_id=None)
    )

    # Audit the deletion (acting admin, deleted user/role, associated ALC/SBU). Never records
    # passwords, hashes or tokens.
    await record_audit(
        db,
        "USER_ACCOUNT_DELETED",
        "user",
        user_id,
        admin,
        request,
        {
            "deleted_user_id": str(user_id),
            "deleted_role": deleted_role.value,
            "alc_id": str(deleted_alc_id) if deleted_alc_id else None,
            "sbu_id": str(deleted_sbu_id) if deleted_sbu_id else None,
            "dcu_id": str(deleted_dcu_id) if deleted_dcu_id else None,
        },
    )
    await db.delete(user)
    await db.commit()
    return {"message": "User account deleted", "deleted_user_id": str(user_id)}


@router.get("/sbus")
async def list_sbus(
    page_data: tuple[int, int] = Depends(pagination),
    search: str | None = None,
    dcu_id: uuid.UUID | None = None,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    page, page_size = page_data
    filters = []
    if search:
        filters.append(or_(SBU.code.ilike(f"%{search}%"), SBU.name.ilike(f"%{search}%")))
    if dcu_id is not None:
        filters.append(SBU.dcu_id == dcu_id)
    total = await db.scalar(select(func.count(SBU.id)).where(*filters)) or 0
    rows = (
        (
            await db.execute(
                select(
                    SBU.id,
                    SBU.code,
                    SBU.name,
                    SBU.is_active,
                    SBU.dcu_id,
                    DCU.code.label("dcu_code"),
                    func.count(ALC.id).label("assigned_alcs"),
                )
                .outerjoin(DCU, SBU.dcu_id == DCU.id)
                .outerjoin(ALC, ALC.sbu_id == SBU.id)
                .where(*filters)
                .group_by(SBU.id, DCU.id)
                .order_by(SBU.code)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .mappings()
        .all()
    )
    return {
        "items": rows,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": math.ceil(total / page_size) if total else 0,
    }


@router.post("/sbus", response_model=SbuOut, status_code=201)
async def create_sbu(
    payload: SbuIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if await db.scalar(select(SBU.id).where(func.lower(SBU.code) == payload.code.lower())):
        raise HTTPException(status_code=409, detail="SBU code already exists")
    if payload.dcu_id is not None and not await db.scalar(
        select(DCU.id).where(DCU.id == payload.dcu_id)
    ):
        raise HTTPException(status_code=422, detail="Invalid DCU")
    sbu = SBU(
        code=payload.code,
        name=payload.name,
        is_active=payload.is_active,
        dcu_id=payload.dcu_id,
    )
    db.add(sbu)
    await db.flush()
    await record_audit(db, "sbu_created", "sbu", sbu.id, admin, request, {"code": sbu.code})
    await db.commit()
    await db.refresh(sbu)
    return sbu


@router.get("/sbus/{sbu_id}")
async def sbu_detail(
    sbu_id: uuid.UUID, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    sbu = await db.get(SBU, sbu_id)
    if not sbu:
        raise HTTPException(status_code=404, detail="SBU not found")
    alcs_rows = (
        await db.execute(
            select(ALC.id, ALC.alc_code, ALC.alc_name, ALC.status)
            .where(ALC.sbu_id == sbu_id)
            .order_by(ALC.alc_code)
        )
    ).mappings().all()
    users_rows = (
        await db.scalars(
            select(User)
            .options(*USER_RESPONSE_LOADERS)
            .where(User.sbu_id == sbu_id, User.role == Role.SBU)
            .order_by(User.username)
        )
    ).all()
    return {
        "sbu": SbuOut.model_validate(sbu),
        "alcs": alcs_rows,
        "users": [UserOut.model_validate(u) for u in users_rows],
    }


@router.patch("/sbus/{sbu_id}", response_model=SbuOut)
async def update_sbu(
    sbu_id: uuid.UUID,
    payload: SbuPatch,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    sbu = await db.get(SBU, sbu_id)
    if not sbu:
        raise HTTPException(status_code=404, detail="SBU not found")
    changes = payload.model_dump(exclude_none=True, exclude={"dcu_id"})
    if payload.name is not None:
        sbu.name = payload.name
    if payload.is_active is not None:
        sbu.is_active = payload.is_active
    if "dcu_id" in payload.model_fields_set:
        # Reassigning an SBU to another DCU moves only this pointer. The SBU keeps its id and
        # every ALC, activity, partner, evidence file and review stays attached; DCU access
        # follows the new hierarchy on the very next request.
        if payload.dcu_id is not None and not await db.scalar(
            select(DCU.id).where(DCU.id == payload.dcu_id)
        ):
            raise HTTPException(status_code=422, detail="Invalid DCU")
        changes["dcu_id"] = str(payload.dcu_id) if payload.dcu_id else None
        changes["previous_dcu_id"] = str(sbu.dcu_id) if sbu.dcu_id else None
        sbu.dcu_id = payload.dcu_id
    await record_audit(db, "sbu_updated", "sbu", sbu.id, admin, request, changes)
    await db.commit()
    await db.refresh(sbu)
    return sbu


@router.get("/dcus")
async def list_dcus(_: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Read-only DCU master with its RCU and hierarchy counts (admin only)."""
    sbu_count = (
        select(func.count(SBU.id)).where(SBU.dcu_id == DCU.id).correlate(DCU).scalar_subquery()
    )
    alc_count = (
        select(func.count(ALC.id))
        .join(SBU, ALC.sbu_id == SBU.id)
        .where(SBU.dcu_id == DCU.id)
        .correlate(DCU)
        .scalar_subquery()
    )
    rows = (
        (
            await db.execute(
                select(
                    DCU.id,
                    DCU.code,
                    DCU.name,
                    DCU.is_active,
                    DCU.rcu_id,
                    RCU.code.label("rcu_code"),
                    RCU.name.label("rcu_name"),
                    sbu_count.label("sbus"),
                    alc_count.label("alcs"),
                )
                .join(RCU, DCU.rcu_id == RCU.id)
                .order_by(DCU.code)
            )
        )
        .mappings()
        .all()
    )
    return {"items": rows, "total": len(rows)}


@router.get("/dcus/{dcu_id}")
async def dcu_detail(
    dcu_id: uuid.UUID, _: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    dcu = await db.get(DCU, dcu_id)
    if not dcu:
        raise HTTPException(status_code=404, detail="DCU not found")
    sbus = (
        (
            await db.execute(
                select(
                    SBU.id,
                    SBU.code,
                    SBU.name,
                    SBU.is_active,
                    func.count(ALC.id).label("assigned_alcs"),
                )
                .outerjoin(ALC, ALC.sbu_id == SBU.id)
                .where(SBU.dcu_id == dcu_id)
                .group_by(SBU.id)
                .order_by(SBU.code)
            )
        )
        .mappings()
        .all()
    )
    users_rows = (
        await db.scalars(
            select(User)
            .options(*USER_RESPONSE_LOADERS)
            .where(User.dcu_id == dcu_id, User.role == Role.DCU)
            .order_by(User.username)
        )
    ).all()
    return {
        "dcu": DcuOut.model_validate(dcu),
        "sbus": sbus,
        "users": [UserOut.model_validate(u) for u in users_rows],
    }


@router.get("/audit-logs")
async def audit_logs(
    page_data: tuple[int, int] = Depends(pagination),
    action: str | None = None,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    page, page_size = page_data
    filters = [AuditLog.action == action] if action else []
    total = await db.scalar(select(func.count(AuditLog.id)).where(*filters)) or 0
    items = (
        await db.scalars(
            select(AuditLog)
            .where(*filters)
            .order_by(desc(AuditLog.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": math.ceil(total / page_size) if total else 0,
    }


@router.get("/reports/activities.csv")
async def report(
    date_from: date | None = None,
    date_to: date | None = None,
    alc_id: uuid.UUID | None = None,
    status: ActivityStatus | None = None,
    activity_type: str | None = None,
    ecosystem: str | None = None,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    filters = queue_filters(status, activity_type, ecosystem, alc_id, date_from, date_to, None)
    rows = (
        await db.execute(
            select(Activity, ALC).join(ALC).where(*filters).order_by(desc(Activity.activity_date))
        )
    ).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Activity Number",
            "ALC Code",
            "ALC Name",
            "Date",
            "Type",
            "Ecosystem",
            "Status",
            "Learners",
            "Leads",
            "Admissions",
        ]
    )
    for activity, alc in rows:
        writer.writerow(
            [
                safe_csv(value)
                for value in [
                    activity.activity_number,
                    alc.alc_code,
                    alc.alc_name,
                    activity.activity_date,
                    activity.activity_type,
                    activity.ecosystem,
                    activity.status.value,
                    activity.learners_reached,
                    activity.leads_generated,
                    activity.admissions_generated,
                ]
            ]
        )
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=activity-report.csv"},
    )


@router.get("/settings")
async def get_settings(_: User = Depends(require_admin)):
    return {
        "max_upload_files": settings.max_upload_files,
        "max_upload_bytes": settings.max_upload_bytes,
        "allowed_types": ["JPG", "JPEG", "PNG", "WEBP", "PDF"],
        "authentication": "HttpOnly cookie + refresh rotation + CSRF",
    }
