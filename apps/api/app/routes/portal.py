"""Unified operational portal API shared by the DCU, SBU and ALC roles.

Every read is scoped server-side through ``app.services.scope`` to the ALCs the
authenticated user may see, resolved from the current hierarchy
(RCU → DCU → SBU → ALC): an ALC user sees only its own centre, an SBU user the
ALCs assigned to its SBU, a DCU user the ALCs of every SBU under its DCU.
ALC-only write endpoints keep the existing ``require_alc`` guard, so ALC
ownership isolation is unchanged. Supervisor endpoints (``require_supervisor``:
DCU or SBU) add review/verification and ALC-directory management. Admin never
reaches these routes.
"""
import csv
import io
import math
import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import case, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import hash_password
from app.config import settings
from app.database import get_db
from app.dependencies import (
    pagination,
    require_alc,
    require_csrf,
    require_portal_user,
    require_supervisor,
)
from app.enums import ActivityStatus, ReviewAction, Role, TaskStatus
from app.models import (
    ALC,
    SBU,
    Activity,
    ActivityEvidence,
    ChallengeProgress,
    Notification,
    Partner,
    Task,
    User,
)
from app.schemas import (
    ActivityIn,
    ActivityOut,
    PartnerIn,
    PartnerOut,
    PasswordResetIn,
    ReviewDecisionIn,
    TaskIn,
    TaskOut,
)
from app.services.activities import EDITABLE_STATUSES, review_activity, submit_activity
from app.services.audit import record_audit
from app.services.csv_export import safe_csv
from app.services.scope import (
    accessible_alc_ids,
    alc_scope,
    can_access_alc,
    can_access_sbu,
    sbu_scope,
)
from app.storage import storage_service

router = APIRouter(prefix="/portal", tags=["Portal"], dependencies=[Depends(require_csrf)])

ALLOWED_MIMES = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/webp": {".webp"},
    "application/pdf": {".pdf"},
}

ACTIVITY_LOADERS = (
    selectinload(Activity.partner),
    selectinload(Activity.evidence),
    selectinload(Activity.reviews),
    selectinload(Activity.revisions),
)


def detect_mime(content: bytes) -> str | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def apply_activity(activity: Activity, payload: ActivityIn) -> None:
    for name, value in payload.model_dump().items():
        setattr(activity, name, value)


async def scoped_alc_ids(user: User, db: AsyncSession) -> Sequence[uuid.UUID]:
    """Return the ALC ids the current portal user may access (see ``services.scope``).

    A missing scope key (DCU without ``dcu_id``, SBU without ``sbu_id``, ALC without
    ``alc_id``) yields an empty scope — never "see everything" and never an
    ``IS NULL`` match that would leak unassigned ALCs. Route guards already reject
    such accounts; scoping defensively here keeps the invariant even if a future
    endpoint is wired without the matching guard.
    """
    return await accessible_alc_ids(db, user)


async def require_alc_in_scope(db: AsyncSession, user: User, alc_id: uuid.UUID) -> None:
    """404 unless a client-supplied ``alc_id`` filter lies inside the caller's scope."""
    if not await can_access_alc(db, user, alc_id):
        raise HTTPException(status_code=404, detail="ALC not found")


async def require_sbu_in_scope(db: AsyncSession, user: User, sbu_id: uuid.UUID) -> None:
    """404 unless a client-supplied ``sbu_id`` filter lies inside the caller's scope."""
    if not await can_access_sbu(db, user, sbu_id):
        raise HTTPException(status_code=404, detail="SBU not found")


def actor_scope(user: User) -> dict:
    """Audit metadata naming the supervisor's own hierarchy key (never a password)."""
    if user.role == Role.DCU:
        return {"dcu_id": str(user.dcu_id)}
    return {"sbu_id": str(user.sbu_id)}


def alcs_of_sbu(sbu_id: uuid.UUID):
    return select(ALC.id).where(ALC.sbu_id == sbu_id).scalar_subquery()


async def scoped_activity(
    db: AsyncSession, user: User, activity_id: uuid.UUID, lock: bool = False
) -> Activity:
    query = (
        select(Activity)
        .options(*ACTIVITY_LOADERS)
        .where(Activity.id == activity_id, alc_scope(user, Activity.alc_id))
    )
    if lock:
        query = query.with_for_update(of=Activity)
    activity = await db.scalar(query)
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")
    return activity


async def scoped_alc(db: AsyncSession, user: User, alc_id: uuid.UUID) -> ALC:
    alc = await db.scalar(
        select(ALC).options(selectinload(ALC.sbu)).where(ALC.id == alc_id, alc_scope(user))
    )
    if not alc:
        raise HTTPException(status_code=404, detail="ALC not found")
    return alc


# --------------------------------------------------------------------------- #
# Dashboards (role-aware)
# --------------------------------------------------------------------------- #
async def challenge_data(user: User, db: AsyncSession) -> dict:
    today = date.today()
    start = today - timedelta(days=29)
    config = await db.scalar(
        select(ChallengeProgress).where(
            ChallengeProgress.alc_id == user.alc_id,
            ChallengeProgress.challenge_period_start <= today,
            ChallengeProgress.challenge_period_end >= today,
        )
    )
    targets = {"prospects": 40, "meetings": 20, "pilots": 10, "partnerships": 5}
    if config:
        start = config.challenge_period_start
        targets = {
            "prospects": config.prospects_target,
            "meetings": config.meetings_target,
            "pilots": config.pilots_target,
            "partnerships": config.partnerships_target,
        }
    verified = [
        Activity.alc_id == user.alc_id,
        Activity.status == ActivityStatus.VERIFIED,
        Activity.activity_date >= start,
        Activity.activity_date <= today,
    ]
    rows = (
        (
            await db.execute(
                select(
                    func.coalesce(func.sum(Activity.leads_generated), 0).label("prospects"),
                    func.count(
                        case((func.lower(Activity.activity_type).like("%meeting%"), 1))
                    ).label("meetings"),
                    func.count(case((func.lower(Activity.activity_type).like("%pilot%"), 1))).label(
                        "pilots"
                    ),
                ).where(*verified)
            )
        )
        .mappings()
        .one()
    )
    partnerships = (
        await db.scalar(
            select(func.count(func.distinct(Activity.partner_id))).where(
                *verified,
                Activity.partner_id.is_not(None),
                func.lower(Activity.activity_type).like("%partnership%"),
            )
        )
        or 0
    )
    return {
        "period_start": start,
        "period_end": config.challenge_period_end if config else today,
        "targets": targets,
        "achieved": {**rows, "partnerships": partnerships},
        "source": "verified activities; partnerships require a linked partner",
    }


async def alc_dashboard(user: User, db: AsyncSession) -> dict:
    base = Activity.alc_id == user.alc_id
    counts = await db.execute(
        select(
            func.count(case((Activity.status != ActivityStatus.DRAFT, 1))).label("activities"),
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
            func.count(case((Activity.status == ActivityStatus.VERIFIED, 1))).label("verified"),
            func.count(case((Activity.status == ActivityStatus.CORRECTION_REQUIRED, 1))).label(
                "corrections"
            ),
            func.count(case((Activity.status == ActivityStatus.REJECTED, 1))).label("rejected"),
            func.coalesce(
                func.sum(
                    case(
                        (Activity.status == ActivityStatus.VERIFIED, Activity.learners_reached),
                        else_=0,
                    )
                ),
                0,
            ).label("learners"),
            func.coalesce(
                func.sum(
                    case(
                        (Activity.status == ActivityStatus.VERIFIED, Activity.leads_generated),
                        else_=0,
                    )
                ),
                0,
            ).label("leads"),
            func.coalesce(
                func.sum(
                    case(
                        (Activity.status == ActivityStatus.VERIFIED, Activity.admissions_generated),
                        else_=0,
                    )
                ),
                0,
            ).label("admissions"),
        ).where(base)
    )
    row = counts.mappings().one()
    active_partners = await db.scalar(
        select(func.count(Partner.id)).where(
            Partner.alc_id == user.alc_id, Partner.status == "ACTIVE"
        )
    )
    recent = (
        await db.scalars(
            select(Activity)
            .options(*ACTIVITY_LOADERS)
            .where(base)
            .order_by(desc(Activity.updated_at))
            .limit(6)
        )
    ).all()
    tasks = (
        await db.scalars(
            select(Task)
            .where(Task.alc_id == user.alc_id, Task.status == TaskStatus.OPEN)
            .order_by(Task.due_date)
            .limit(6)
        )
    ).all()
    notifications = (
        await db.scalars(
            select(Notification)
            .where(Notification.user_id == user.id)
            .order_by(desc(Notification.created_at))
            .limit(8)
        )
    ).all()
    return {
        "role": Role.ALC.value,
        **row,
        "active_partners": active_partners or 0,
        "recent_activities": [ActivityOut.model_validate(x) for x in recent],
        "upcoming_tasks": [TaskOut.model_validate(x) for x in tasks],
        "notifications": notifications,
        "challenge": await challenge_data(user, db),
    }


async def supervisor_dashboard(user: User, db: AsyncSession) -> dict:
    """Dashboard for DCU and SBU users over every ALC in their hierarchy scope."""
    in_scope = alc_scope(user)
    assigned = await db.scalar(select(func.count(ALC.id)).where(in_scope)) or 0
    sbu_count = await db.scalar(select(func.count(SBU.id)).where(sbu_scope(user))) or 0
    active_alcs = 0
    partner_count = 0
    if not assigned:
        metrics = {
            "activities": 0,
            "submitted": 0,
            "pending": 0,
            "verified": 0,
            "corrections": 0,
            "rejected": 0,
            "learners": 0,
            "leads": 0,
            "admissions": 0,
        }
        recent: list = []
    else:
        active_alcs = (
            await db.scalar(
                select(func.count(ALC.id)).where(in_scope, ALC.status == "ACTIVE")
            )
        ) or 0
        partner_count = (
            await db.scalar(
                select(func.count(Partner.id)).where(alc_scope(user, Partner.alc_id))
            )
        ) or 0
        base = alc_scope(user, Activity.alc_id)
        row = (
            await db.execute(
                select(
                    func.count(case((Activity.status != ActivityStatus.DRAFT, 1))).label(
                        "activities"
                    ),
                    func.count(case((Activity.status == ActivityStatus.SUBMITTED, 1))).label(
                        "submitted"
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
                    func.count(case((Activity.status == ActivityStatus.VERIFIED, 1))).label(
                        "verified"
                    ),
                    func.count(
                        case((Activity.status == ActivityStatus.CORRECTION_REQUIRED, 1))
                    ).label("corrections"),
                    func.count(case((Activity.status == ActivityStatus.REJECTED, 1))).label(
                        "rejected"
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
                    ).label("learners"),
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
                    ).label("leads"),
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
                    ).label("admissions"),
                ).where(base)
            )
        ).mappings().one()
        metrics = dict(row)
        rows = (
            await db.execute(
                select(Activity, ALC)
                .join(ALC, Activity.alc_id == ALC.id)
                .options(*ACTIVITY_LOADERS)
                .where(base, Activity.status != ActivityStatus.DRAFT)
                .order_by(desc(Activity.submitted_at), desc(Activity.updated_at))
                .limit(8)
            )
        ).all()
        recent = [
            {
                "activity": ActivityOut.model_validate(activity),
                "alc": {"id": alc.id, "alc_code": alc.alc_code, "alc_name": alc.alc_name},
            }
            for activity, alc in rows
        ]
    return {
        "role": user.role.value,
        "sbus": sbu_count,
        "assigned_alcs": assigned,
        "active_alcs": active_alcs,
        "partners": partner_count,
        **metrics,
        "recent_activities": recent,
    }


@router.get("/dashboard")
async def dashboard(user: User = Depends(require_portal_user), db: AsyncSession = Depends(get_db)):
    if user.role in (Role.DCU, Role.SBU):
        return await supervisor_dashboard(user, db)
    return await alc_dashboard(user, db)


# --------------------------------------------------------------------------- #
# Activities (shared read; ALC-only writes)
# --------------------------------------------------------------------------- #
@router.get("/activities")
async def list_activities(
    page_data: tuple[int, int] = Depends(pagination),
    status: ActivityStatus | None = None,
    activity_type: str | None = None,
    alc_id: uuid.UUID | None = None,
    sbu_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
    user: User = Depends(require_portal_user),
    db: AsyncSession = Depends(get_db),
):
    page, page_size = page_data
    filters = [alc_scope(user, Activity.alc_id)]
    if alc_id is not None:
        await require_alc_in_scope(db, user, alc_id)
        filters.append(Activity.alc_id == alc_id)
    if sbu_id is not None:
        await require_sbu_in_scope(db, user, sbu_id)
        filters.append(Activity.alc_id.in_(alcs_of_sbu(sbu_id)))
    if status:
        filters.append(Activity.status == status)
    if activity_type:
        filters.append(Activity.activity_type == activity_type)
    if date_from:
        filters.append(Activity.activity_date >= date_from)
    if date_to:
        filters.append(Activity.activity_date <= date_to)
    if search:
        filters.append(
            or_(
                Activity.activity_number.ilike(f"%{search}%"),
                Activity.activity_type.ilike(f"%{search}%"),
            )
        )
    total = await db.scalar(select(func.count(Activity.id)).where(*filters)) or 0
    items = (
        await db.scalars(
            select(Activity)
            .options(*ACTIVITY_LOADERS)
            .where(*filters)
            .order_by(desc(Activity.updated_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {
        "items": [ActivityOut.model_validate(x) for x in items],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": math.ceil(total / page_size) if total else 0,
    }


@router.post("/activities", response_model=ActivityOut, status_code=201)
async def create_activity(
    payload: ActivityIn,
    request: Request,
    user: User = Depends(require_alc),
    db: AsyncSession = Depends(get_db),
):
    if payload.partner_id and not await db.scalar(
        select(Partner.id).where(Partner.id == payload.partner_id, Partner.alc_id == user.alc_id)
    ):
        raise HTTPException(status_code=422, detail="Invalid partner")
    activity_id = uuid.uuid4()
    activity = Activity(
        id=activity_id,
        activity_number=f"ACT-{date.today().year}-{activity_id.hex[:16].upper()}",
        alc_id=user.alc_id,
        created_by=user.id,
    )
    apply_activity(activity, payload)
    db.add(activity)
    await record_audit(db, "activity_created", "activity", activity.id, user, request)
    await db.commit()
    return await scoped_activity(db, user, activity.id)


@router.get("/activities/{activity_id}", response_model=ActivityOut)
async def get_activity(
    activity_id: uuid.UUID,
    user: User = Depends(require_portal_user),
    db: AsyncSession = Depends(get_db),
):
    return await scoped_activity(db, user, activity_id)


@router.patch("/activities/{activity_id}", response_model=ActivityOut)
async def update_activity(
    activity_id: uuid.UUID,
    payload: ActivityIn,
    request: Request,
    user: User = Depends(require_alc),
    db: AsyncSession = Depends(get_db),
):
    activity = await scoped_activity(db, user, activity_id, lock=True)
    if activity.status not in EDITABLE_STATUSES:
        raise HTTPException(status_code=409, detail="Activity cannot be modified")
    if payload.partner_id and not await db.scalar(
        select(Partner.id).where(Partner.id == payload.partner_id, Partner.alc_id == user.alc_id)
    ):
        raise HTTPException(status_code=422, detail="Invalid partner")
    apply_activity(activity, payload)
    await record_audit(db, "activity_updated", "activity", activity.id, user, request)
    await db.commit()
    return await scoped_activity(db, user, activity.id)


@router.post("/activities/{activity_id}/submit", response_model=ActivityOut)
async def submit(
    activity_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_alc),
    db: AsyncSession = Depends(get_db),
):
    activity = await scoped_activity(db, user, activity_id, lock=True)
    previous = activity.status
    await submit_activity(db, activity, user)
    if previous == ActivityStatus.CORRECTION_REQUIRED:
        # Notify the reviewers of this ALC under the *current* hierarchy: every admin, the
        # SBU users of its SBU, and the DCU users of that SBU's DCU.
        alc_sbu_id, alc_dcu_id = (
            await db.execute(
                select(ALC.sbu_id, SBU.dcu_id)
                .outerjoin(SBU, ALC.sbu_id == SBU.id)
                .where(ALC.id == activity.alc_id)
            )
        ).one()
        recipient_conditions = [User.role == Role.ADMIN]
        if alc_sbu_id is not None:
            recipient_conditions.append(
                (User.role == Role.SBU) & (User.sbu_id == alc_sbu_id)
            )
        if alc_dcu_id is not None:
            recipient_conditions.append(
                (User.role == Role.DCU) & (User.dcu_id == alc_dcu_id)
            )
        reviewers = (
            await db.scalars(
                select(User).where(User.is_active.is_(True), or_(*recipient_conditions))
            )
        ).all()
        centre_name = user.alc.alc_name if user.alc else "ALC"
        for reviewer in reviewers:
            db.add(
                Notification(
                    user_id=reviewer.id,
                    title="Activity resubmitted",
                    message=f"{activity.activity_number} from {centre_name} is ready for review",
                    type="RESUBMITTED",
                    entity_type="activity",
                    entity_id=str(activity.id),
                )
            )
    await record_audit(
        db,
        "activity_resubmitted"
        if previous == ActivityStatus.CORRECTION_REQUIRED
        else "activity_submitted",
        "activity",
        activity.id,
        user,
        request,
    )
    await db.commit()
    db.expire(activity, ["reviews", "revisions"])
    return await scoped_activity(db, user, activity.id)


# --------------------------------------------------------------------------- #
# Evidence (shared read; ALC-only writes)
# --------------------------------------------------------------------------- #
async def scoped_evidence(
    db: AsyncSession, user: User, evidence_id: uuid.UUID
) -> ActivityEvidence:
    evidence = await db.scalar(
        select(ActivityEvidence)
        .join(Activity)
        .where(
            ActivityEvidence.id == evidence_id,
            alc_scope(user, Activity.alc_id),
            ActivityEvidence.is_active.is_(True),
        )
    )
    if not evidence:
        raise HTTPException(status_code=404, detail="Evidence not found")
    return evidence


@router.post("/activities/{activity_id}/evidence", response_model=list[dict], status_code=201)
async def upload_evidence(
    activity_id: uuid.UUID,
    request: Request,
    files: list[UploadFile] = File(...),
    user: User = Depends(require_alc),
    db: AsyncSession = Depends(get_db),
):
    activity = await scoped_activity(db, user, activity_id)
    if activity.status not in EDITABLE_STATUSES:
        raise HTTPException(status_code=409, detail="Evidence is locked")
    active_count = sum(1 for item in activity.evidence if item.is_active)
    if not files or active_count + len(files) > settings.max_upload_files:
        raise HTTPException(
            status_code=422, detail=f"Maximum {settings.max_upload_files} evidence files allowed"
        )
    validated = []
    for file in files:
        content = await file.read(settings.max_upload_bytes + 1)
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail=f"{file.filename}: file too large")
        detected = detect_mime(content)
        extension = Path(file.filename or "").suffix.lower()
        if (
            not detected
            or extension not in ALLOWED_MIMES[detected]
            or file.content_type != detected
        ):
            raise HTTPException(
                status_code=415, detail=f"{file.filename}: unsupported or mismatched file type"
            )
        validated.append((file, content, detected, extension))
    uploaded = []
    for file, content, detected, extension in validated:
        evidence_id = uuid.uuid4()
        key = f"evidence/{user.alc_id}/{activity.id}/{evidence_id}{extension}"
        await storage_service.upload(key, content, detected)
        evidence = ActivityEvidence(
            id=evidence_id,
            activity_id=activity.id,
            storage_key=key,
            original_filename=(file.filename or "evidence")[:255],
            mime_type=detected,
            file_size=len(content),
            uploaded_by=user.id,
        )
        db.add(evidence)
        uploaded.append(
            {
                "id": evidence_id,
                "original_filename": evidence.original_filename,
                "mime_type": detected,
                "file_size": len(content),
            }
        )
        await record_audit(db, "evidence_uploaded", "evidence", evidence_id, user, request)
    await db.commit()
    return uploaded


@router.delete("/evidence/{evidence_id}")
async def delete_evidence(
    evidence_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_alc),
    db: AsyncSession = Depends(get_db),
):
    evidence = await scoped_evidence(db, user, evidence_id)
    activity = await scoped_activity(db, user, evidence.activity_id)
    if activity.status not in EDITABLE_STATUSES:
        raise HTTPException(status_code=409, detail="Evidence is locked")
    evidence.is_active = False
    await storage_service.delete(evidence.storage_key)
    await record_audit(db, "evidence_deleted", "evidence", evidence.id, user, request)
    await db.commit()
    return {"message": "Evidence removed"}


@router.get("/evidence/{evidence_id}/access")
async def evidence_access(
    evidence_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_portal_user),
    db: AsyncSession = Depends(get_db),
):
    evidence = await scoped_evidence(db, user, evidence_id)
    if settings.storage_backend == "local":
        return {
            "url": f"{str(request.base_url).rstrip('/')}/api/portal/evidence/{evidence_id}/content",
            "expires_in": 0,
        }
    return {
        "url": await storage_service.get_secure_url(evidence.storage_key),
        "expires_in": settings.s3_presign_seconds,
    }


@router.get("/evidence/{evidence_id}/content")
async def evidence_content(
    evidence_id: uuid.UUID,
    user: User = Depends(require_portal_user),
    db: AsyncSession = Depends(get_db),
):
    if settings.storage_backend != "local":
        raise HTTPException(status_code=404, detail="Evidence not found")
    # Authorize (DCU/SBU/ALC scoping) before touching the filesystem, then treat a
    # missing local file as a 404 rather than letting FileNotFoundError escape as a 500.
    evidence = await scoped_evidence(db, user, evidence_id)
    try:
        content = await storage_service.get(evidence.storage_key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Evidence not found") from None
    return Response(
        content,
        media_type=evidence.mime_type,
        headers={"Cache-Control": "private, no-store"},
    )


# --------------------------------------------------------------------------- #
# Partners (shared read; ALC-only writes)
# --------------------------------------------------------------------------- #
@router.get("/partners", response_model=list[PartnerOut])
async def partners(
    user: User = Depends(require_portal_user), db: AsyncSession = Depends(get_db)
):
    return (
        await db.scalars(
            select(Partner)
            .where(alc_scope(user, Partner.alc_id))
            .order_by(Partner.partner_name)
        )
    ).all()


@router.post("/partners", response_model=PartnerOut, status_code=201)
async def create_partner(
    payload: PartnerIn, user: User = Depends(require_alc), db: AsyncSession = Depends(get_db)
):
    partner = Partner(alc_id=user.alc_id, **payload.model_dump())
    db.add(partner)
    await db.commit()
    await db.refresh(partner)
    return partner


@router.patch("/partners/{partner_id}", response_model=PartnerOut)
async def update_partner(
    partner_id: uuid.UUID,
    payload: PartnerIn,
    user: User = Depends(require_alc),
    db: AsyncSession = Depends(get_db),
):
    partner = await db.scalar(
        select(Partner).where(Partner.id == partner_id, Partner.alc_id == user.alc_id)
    )
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    for name, value in payload.model_dump().items():
        setattr(partner, name, value)
    await db.commit()
    await db.refresh(partner)
    return partner


# --------------------------------------------------------------------------- #
# Tasks / challenge / notifications (ALC-only)
# --------------------------------------------------------------------------- #
@router.get("/tasks", response_model=list[TaskOut])
async def tasks(user: User = Depends(require_alc), db: AsyncSession = Depends(get_db)):
    return (
        await db.scalars(
            select(Task).where(Task.alc_id == user.alc_id).order_by(Task.status, Task.due_date)
        )
    ).all()


@router.post("/tasks", response_model=TaskOut, status_code=201)
async def create_task(
    payload: TaskIn, user: User = Depends(require_alc), db: AsyncSession = Depends(get_db)
):
    if payload.partner_id and not await db.scalar(
        select(Partner.id).where(Partner.id == payload.partner_id, Partner.alc_id == user.alc_id)
    ):
        raise HTTPException(status_code=422, detail="Invalid partner")
    task = Task(alc_id=user.alc_id, **payload.model_dump())
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


@router.patch("/tasks/{task_id}/complete", response_model=TaskOut)
async def complete_task(
    task_id: uuid.UUID, user: User = Depends(require_alc), db: AsyncSession = Depends(get_db)
):
    task = await db.scalar(select(Task).where(Task.id == task_id, Task.alc_id == user.alc_id))
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = TaskStatus.COMPLETED
    await db.commit()
    await db.refresh(task)
    return task


@router.get("/challenge")
async def challenge(user: User = Depends(require_alc), db: AsyncSession = Depends(get_db)):
    return await challenge_data(user, db)


@router.get("/notifications")
async def notifications(
    user: User = Depends(require_portal_user), db: AsyncSession = Depends(get_db)
):
    return (
        await db.scalars(
            select(Notification)
            .where(Notification.user_id == user.id)
            .order_by(desc(Notification.created_at))
            .limit(100)
        )
    ).all()


@router.post("/notifications/{notification_id}/read")
async def mark_read(
    notification_id: uuid.UUID,
    user: User = Depends(require_portal_user),
    db: AsyncSession = Depends(get_db),
):
    item = await db.scalar(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user.id
        )
    )
    if not item:
        raise HTTPException(status_code=404, detail="Notification not found")
    item.is_read = True
    await db.commit()
    return {"message": "Marked read"}


# --------------------------------------------------------------------------- #
# Supervisor (DCU / SBU): SBU and ALC directory, ALC detail, partners
# --------------------------------------------------------------------------- #
@router.get("/sbus")
async def supervisor_sbus(
    user: User = Depends(require_supervisor), db: AsyncSession = Depends(get_db)
):
    """SBUs in the caller's scope: every SBU under a DCU user's DCU, or an SBU user's own."""
    alc_count = (
        select(func.count(ALC.id)).where(ALC.sbu_id == SBU.id).correlate(SBU).scalar_subquery()
    )
    rows = (
        (
            await db.execute(
                select(
                    SBU.id,
                    SBU.code,
                    SBU.name,
                    SBU.is_active,
                    SBU.dcu_id,
                    alc_count.label("assigned_alcs"),
                )
                .where(sbu_scope(user))
                .order_by(SBU.code)
            )
        )
        .mappings()
        .all()
    )
    return {"items": rows, "total": len(rows)}


@router.get("/sbus/{sbu_id}")
async def supervisor_sbu_detail(
    sbu_id: uuid.UUID, user: User = Depends(require_supervisor), db: AsyncSession = Depends(get_db)
):
    sbu = await db.scalar(
        select(SBU).options(selectinload(SBU.dcu)).where(SBU.id == sbu_id, sbu_scope(user))
    )
    if not sbu:
        raise HTTPException(status_code=404, detail="SBU not found")
    alcs_rows = (
        (
            await db.execute(
                select(ALC.id, ALC.alc_code, ALC.alc_name, ALC.status)
                .where(ALC.sbu_id == sbu.id, alc_scope(user))
                .order_by(ALC.alc_code)
            )
        )
        .mappings()
        .all()
    )
    return {
        "sbu": {
            "id": sbu.id,
            "code": sbu.code,
            "name": sbu.name,
            "is_active": sbu.is_active,
            "dcu": (
                {"id": sbu.dcu.id, "code": sbu.dcu.code, "name": sbu.dcu.name}
                if sbu.dcu
                else None
            ),
        },
        "alcs": alcs_rows,
    }


@router.get("/alcs")
async def supervisor_alcs(
    page_data: tuple[int, int] = Depends(pagination),
    search: str | None = None,
    status: str | None = None,
    sbu_id: uuid.UUID | None = None,
    user: User = Depends(require_supervisor),
    db: AsyncSession = Depends(get_db),
):
    page, page_size = page_data
    filters = [alc_scope(user)]
    if sbu_id is not None:
        await require_sbu_in_scope(db, user, sbu_id)
        filters.append(ALC.sbu_id == sbu_id)
    if search:
        filters.append(or_(ALC.alc_code.ilike(f"%{search}%"), ALC.alc_name.ilike(f"%{search}%")))
    if status:
        filters.append(ALC.status == status)
    partner_count = (
        select(func.count(Partner.id))
        .where(Partner.alc_id == ALC.id)
        .correlate(ALC)
        .scalar_subquery()
    )
    total = await db.scalar(select(func.count(ALC.id)).where(*filters)) or 0
    rows = (
        (
            await db.execute(
                select(
                    ALC.id,
                    ALC.alc_code,
                    ALC.alc_name,
                    ALC.status,
                    ALC.sbu_id,
                    SBU.code.label("sbu_code"),
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
                    partner_count.label("partners"),
                    func.max(Activity.activity_date).label("last_activity"),
                )
                .outerjoin(SBU, ALC.sbu_id == SBU.id)
                .outerjoin(Activity, ALC.id == Activity.alc_id)
                .where(*filters)
                .group_by(ALC.id, SBU.id)
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
async def supervisor_alc_detail(
    alc_id: uuid.UUID, user: User = Depends(require_supervisor), db: AsyncSession = Depends(get_db)
):
    alc = await scoped_alc(db, user, alc_id)
    activities = (
        await db.scalars(
            select(Activity)
            .options(*ACTIVITY_LOADERS)
            .where(Activity.alc_id == alc_id)
            .order_by(desc(Activity.updated_at))
            .limit(100)
        )
    ).all()
    partners_list = (
        await db.scalars(
            select(Partner).where(Partner.alc_id == alc_id).order_by(Partner.partner_name)
        )
    ).all()
    return {
        "alc": {
            "id": alc.id,
            "alc_code": alc.alc_code,
            "alc_name": alc.alc_name,
            "status": alc.status,
            "sbu": (
                {"id": alc.sbu.id, "code": alc.sbu.code, "name": alc.sbu.name}
                if alc.sbu
                else None
            ),
        },
        "activities": [ActivityOut.model_validate(x) for x in activities],
        "partners": partners_list,
    }


@router.post("/alcs/{alc_id}/reset-password")
async def supervisor_reset_alc_password(
    alc_id: uuid.UUID,
    payload: PasswordResetIn,
    request: Request,
    user: User = Depends(require_supervisor),
    db: AsyncSession = Depends(get_db),
):
    """Reset the login of an ALC inside the caller's scope. Supervisors can only reset ALC
    logins — never SBU, DCU or ADMIN accounts (user management stays admin-only)."""
    alc = await scoped_alc(db, user, alc_id)
    target = await db.scalar(
        select(User).where(User.alc_id == alc.id, User.role == Role.ALC).order_by(User.username)
    )
    if not target:
        raise HTTPException(status_code=404, detail="ALC user not found")
    target.password_hash = hash_password(payload.password)
    target.must_change_password = True
    await record_audit(
        db,
        "alc_password_reset",
        "user",
        target.id,
        user,
        request,
        {"alc_id": str(alc.id), **actor_scope(user), "password_reset": True},
    )
    await db.commit()
    return {"message": "Password reset", "user_id": target.id}


@router.get("/sbu/partners")
async def supervisor_partners(
    user: User = Depends(require_supervisor), db: AsyncSession = Depends(get_db)
):
    """Partners across the caller's in-scope ALCs, with activity count and last activity."""
    activity_count = (
        select(func.count(Activity.id))
        .where(Activity.partner_id == Partner.id)
        .correlate(Partner)
        .scalar_subquery()
    )
    last_activity = (
        select(func.max(Activity.activity_date))
        .where(Activity.partner_id == Partner.id)
        .correlate(Partner)
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(
                Partner,
                ALC.alc_code,
                ALC.alc_name,
                activity_count.label("activity_count"),
                last_activity.label("last_activity"),
            )
            .join(ALC, Partner.alc_id == ALC.id)
            .where(alc_scope(user, Partner.alc_id))
            .order_by(Partner.partner_name)
        )
    ).all()
    return [
        {
            "id": partner.id,
            "alc_id": partner.alc_id,
            "alc_code": alc_code,
            "alc_name": alc_name,
            "partner_name": partner.partner_name,
            "partner_type": partner.partner_type,
            "ecosystem": partner.ecosystem,
            "contact_person": partner.contact_person,
            "phone": partner.phone,
            "email": partner.email,
            "status": partner.status,
            "activity_count": activities,
            "last_activity": last,
        }
        for partner, alc_code, alc_name, activities, last in rows
    ]


# --------------------------------------------------------------------------- #
# Supervisor (DCU / SBU): verification queue and review decisions
# --------------------------------------------------------------------------- #
@router.get("/verification")
async def supervisor_verification_queue(
    page_data: tuple[int, int] = Depends(pagination),
    status: ActivityStatus | None = None,
    alc_id: uuid.UUID | None = None,
    sbu_id: uuid.UUID | None = None,
    activity_type: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
    queue_only: bool = False,
    user: User = Depends(require_supervisor),
    db: AsyncSession = Depends(get_db),
):
    page, page_size = page_data
    filters = [alc_scope(user, Activity.alc_id)]
    if alc_id is not None:
        await require_alc_in_scope(db, user, alc_id)
        filters.append(Activity.alc_id == alc_id)
    if sbu_id is not None:
        await require_sbu_in_scope(db, user, sbu_id)
        filters.append(Activity.alc_id.in_(alcs_of_sbu(sbu_id)))
    if queue_only and not status:
        filters.append(
            Activity.status.in_(
                [
                    ActivityStatus.SUBMITTED,
                    ActivityStatus.RESUBMITTED,
                    ActivityStatus.UNDER_REVIEW,
                ]
            )
        )
    elif status:
        filters.append(Activity.status == status)
    if activity_type:
        filters.append(Activity.activity_type == activity_type)
    if date_from:
        filters.append(Activity.activity_date >= date_from)
    if date_to:
        filters.append(Activity.activity_date <= date_to)
    if search:
        filters.append(
            or_(
                Activity.activity_number.ilike(f"%{search}%"),
                ALC.alc_code.ilike(f"%{search}%"),
                ALC.alc_name.ilike(f"%{search}%"),
            )
        )
    total = (
        await db.scalar(
            select(func.count(Activity.id)).join(ALC, Activity.alc_id == ALC.id).where(*filters)
        )
        or 0
    )
    rows = (
        await db.execute(
            select(Activity, ALC)
            .join(ALC, Activity.alc_id == ALC.id)
            .options(*ACTIVITY_LOADERS)
            .where(*filters)
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


@router.get("/verification/{activity_id}")
async def supervisor_review_detail(
    activity_id: uuid.UUID,
    user: User = Depends(require_supervisor),
    db: AsyncSession = Depends(get_db),
):
    activity = await scoped_activity(db, user, activity_id)
    alc = await db.get(ALC, activity.alc_id)
    return {
        "activity": ActivityOut.model_validate(activity),
        "alc": {
            "id": alc.id,
            "alc_code": alc.alc_code,
            "alc_name": alc.alc_name,
            "status": alc.status,
        },
    }


async def supervisor_decision(
    activity_id: uuid.UUID,
    payload: ReviewDecisionIn,
    action: ReviewAction,
    request: Request,
    user: User,
    db: AsyncSession,
) -> ActivityOut:
    activity = await scoped_activity(db, user, activity_id, lock=True)
    await review_activity(db, activity, user, action, payload.remark)
    await record_audit(
        db,
        action.value.lower(),
        "activity",
        activity.id,
        user,
        request,
        {"remark": payload.remark, "alc_id": str(activity.alc_id), **actor_scope(user)},
    )
    await db.commit()
    db.expire(activity, ["reviews", "revisions"])
    return await scoped_activity(db, user, activity.id)


@router.post("/activities/{activity_id}/verify", response_model=ActivityOut)
async def supervisor_verify(
    activity_id: uuid.UUID,
    payload: ReviewDecisionIn,
    request: Request,
    user: User = Depends(require_supervisor),
    db: AsyncSession = Depends(get_db),
):
    return await supervisor_decision(activity_id, payload, ReviewAction.VERIFY, request, user, db)


@router.post("/activities/{activity_id}/request-correction", response_model=ActivityOut)
async def supervisor_request_correction(
    activity_id: uuid.UUID,
    payload: ReviewDecisionIn,
    request: Request,
    user: User = Depends(require_supervisor),
    db: AsyncSession = Depends(get_db),
):
    return await supervisor_decision(
        activity_id, payload, ReviewAction.REQUEST_CORRECTION, request, user, db
    )


@router.post("/activities/{activity_id}/reject", response_model=ActivityOut)
async def supervisor_reject(
    activity_id: uuid.UUID,
    payload: ReviewDecisionIn,
    request: Request,
    user: User = Depends(require_supervisor),
    db: AsyncSession = Depends(get_db),
):
    return await supervisor_decision(activity_id, payload, ReviewAction.REJECT, request, user, db)


# --------------------------------------------------------------------------- #
# Reports (role-scoped)
# --------------------------------------------------------------------------- #
@router.get("/reports/activities.csv")
async def export_activities(
    date_from: date | None = None,
    date_to: date | None = None,
    alc_id: uuid.UUID | None = None,
    sbu_id: uuid.UUID | None = None,
    status: ActivityStatus | None = None,
    activity_type: str | None = None,
    ecosystem: str | None = None,
    user: User = Depends(require_portal_user),
    db: AsyncSession = Depends(get_db),
):
    filters = [alc_scope(user, Activity.alc_id)]
    if alc_id is not None:
        await require_alc_in_scope(db, user, alc_id)
        filters.append(Activity.alc_id == alc_id)
    if sbu_id is not None:
        await require_sbu_in_scope(db, user, sbu_id)
        filters.append(Activity.alc_id.in_(alcs_of_sbu(sbu_id)))
    if status:
        filters.append(Activity.status == status)
    if activity_type:
        filters.append(Activity.activity_type == activity_type)
    if ecosystem:
        filters.append(Activity.ecosystem == ecosystem)
    if date_from:
        filters.append(Activity.activity_date >= date_from)
    if date_to:
        filters.append(Activity.activity_date <= date_to)
    rows = (
        await db.execute(
            select(Activity, ALC)
            .join(ALC, Activity.alc_id == ALC.id)
            .where(*filters)
            .order_by(desc(Activity.activity_date))
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
        headers={"Content-Disposition": "attachment; filename=portal-activities.csv"},
    )


@router.get("/reports/partners.csv")
async def export_partners(
    alc_id: uuid.UUID | None = None,
    user: User = Depends(require_portal_user),
    db: AsyncSession = Depends(get_db),
):
    """Partner report scoped to the caller's ALCs (DCU: region; SBU: assigned; ALC: own)."""
    filters = [alc_scope(user, Partner.alc_id)]
    if alc_id is not None:
        await require_alc_in_scope(db, user, alc_id)
        filters.append(Partner.alc_id == alc_id)
    activity_count = (
        select(func.count(Activity.id))
        .where(Activity.partner_id == Partner.id)
        .correlate(Partner)
        .scalar_subquery()
    )
    last_activity = (
        select(func.max(Activity.activity_date))
        .where(Activity.partner_id == Partner.id)
        .correlate(Partner)
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(
                Partner,
                ALC.alc_code,
                ALC.alc_name,
                activity_count.label("activity_count"),
                last_activity.label("last_activity"),
            )
            .join(ALC, Partner.alc_id == ALC.id)
            .where(*filters)
            .order_by(ALC.alc_code, Partner.partner_name)
        )
    ).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "ALC Code",
            "ALC Name",
            "Partner",
            "Type",
            "Ecosystem",
            "Contact",
            "Phone",
            "Email",
            "Status",
            "Activities",
            "Last Activity",
        ]
    )
    for partner, alc_code, alc_name, activities, last in rows:
        writer.writerow(
            [
                safe_csv(value)
                for value in [
                    alc_code,
                    alc_name,
                    partner.partner_name,
                    partner.partner_type,
                    partner.ecosystem,
                    partner.contact_person or "",
                    partner.phone or "",
                    partner.email or "",
                    partner.status,
                    activities,
                    last or "",
                ]
            ]
        )
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=portal-partners.csv"},
    )


@router.get("/reports/verification-status.csv")
async def export_verification_status(
    user: User = Depends(require_portal_user),
    db: AsyncSession = Depends(get_db),
):
    """Per-ALC verification-status summary scoped to the caller's ALCs."""
    rows = (
        (
            await db.execute(
                select(
                    ALC.alc_code,
                    ALC.alc_name,
                    func.count(case((Activity.status != ActivityStatus.DRAFT, 1))).label(
                        "submitted"
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
                    func.count(case((Activity.status == ActivityStatus.VERIFIED, 1))).label(
                        "verified"
                    ),
                    func.count(
                        case((Activity.status == ActivityStatus.CORRECTION_REQUIRED, 1))
                    ).label("correction"),
                    func.count(case((Activity.status == ActivityStatus.REJECTED, 1))).label(
                        "rejected"
                    ),
                    func.coalesce(
                        func.sum(
                            case(
                                (Activity.status == ActivityStatus.VERIFIED, Activity.learners_reached),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("learners"),
                    func.coalesce(
                        func.sum(
                            case(
                                (Activity.status == ActivityStatus.VERIFIED, Activity.leads_generated),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("leads"),
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
                    ).label("admissions"),
                )
                .outerjoin(Activity, ALC.id == Activity.alc_id)
                .where(alc_scope(user))
                .group_by(ALC.id)
                .order_by(ALC.alc_code)
            )
        )
        .mappings()
        .all()
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "ALC Code",
            "ALC Name",
            "Submitted",
            "Pending",
            "Verified",
            "Correction Required",
            "Rejected",
            "Verified Learners",
            "Verified Leads",
            "Verified Admissions",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                safe_csv(value)
                for value in [
                    row["alc_code"],
                    row["alc_name"],
                    row["submitted"],
                    row["pending"],
                    row["verified"],
                    row["correction"],
                    row["rejected"],
                    row["learners"],
                    row["leads"],
                    row["admissions"],
                ]
            ]
        )
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=portal-verification-status.csv"},
    )