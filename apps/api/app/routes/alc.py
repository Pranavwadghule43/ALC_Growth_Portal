import csv
import io
import math
import uuid
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import case, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.dependencies import pagination, require_alc, require_csrf
from app.enums import ActivityStatus, Role, TaskStatus
from app.models import (
    Activity,
    ActivityEvidence,
    ChallengeProgress,
    Notification,
    Partner,
    Task,
    User,
)
from app.schemas import ActivityIn, ActivityOut, PartnerIn, PartnerOut, TaskIn, TaskOut
from app.services.activities import EDITABLE_STATUSES, owned_activity, submit_activity
from app.services.audit import record_audit
from app.services.csv_export import safe_csv
from app.storage import storage_service

router = APIRouter(prefix="/alc", tags=["ALC"], dependencies=[Depends(require_csrf)])
ALLOWED_MIMES = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/webp": {".webp"},
    "application/pdf": {".pdf"},
}


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


@router.get("/dashboard")
async def dashboard(user: User = Depends(require_alc), db: AsyncSession = Depends(get_db)):
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
            .options(
                selectinload(Activity.partner),
                selectinload(Activity.evidence),
                selectinload(Activity.reviews),
                selectinload(Activity.revisions),
            )
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
    challenge = await challenge_data(user, db)
    return {
        **row,
        "active_partners": active_partners or 0,
        "recent_activities": [ActivityOut.model_validate(x) for x in recent],
        "upcoming_tasks": [TaskOut.model_validate(x) for x in tasks],
        "notifications": notifications,
        "challenge": challenge,
    }


@router.get("/activities")
async def list_activities(
    page_data: tuple[int, int] = Depends(pagination),
    status: ActivityStatus | None = None,
    activity_type: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
    user: User = Depends(require_alc),
    db: AsyncSession = Depends(get_db),
):
    page, page_size = page_data
    filters = [Activity.alc_id == user.alc_id]
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
            .options(
                selectinload(Activity.partner),
                selectinload(Activity.evidence),
                selectinload(Activity.reviews),
                selectinload(Activity.revisions),
            )
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
    return await owned_activity(db, activity.id, user.alc_id)


@router.get("/activities/{activity_id}", response_model=ActivityOut)
async def get_activity(
    activity_id: uuid.UUID, user: User = Depends(require_alc), db: AsyncSession = Depends(get_db)
):
    return await owned_activity(db, activity_id, user.alc_id)


@router.patch("/activities/{activity_id}", response_model=ActivityOut)
async def update_activity(
    activity_id: uuid.UUID,
    payload: ActivityIn,
    request: Request,
    user: User = Depends(require_alc),
    db: AsyncSession = Depends(get_db),
):
    activity = await owned_activity(db, activity_id, user.alc_id, lock=True)
    if activity.status not in EDITABLE_STATUSES:
        raise HTTPException(status_code=409, detail="Activity cannot be modified")
    if payload.partner_id and not await db.scalar(
        select(Partner.id).where(Partner.id == payload.partner_id, Partner.alc_id == user.alc_id)
    ):
        raise HTTPException(status_code=422, detail="Invalid partner")
    apply_activity(activity, payload)
    await record_audit(db, "activity_updated", "activity", activity.id, user, request)
    await db.commit()
    return await owned_activity(db, activity.id, user.alc_id)


@router.post("/activities/{activity_id}/submit", response_model=ActivityOut)
async def submit(
    activity_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_alc),
    db: AsyncSession = Depends(get_db),
):
    activity = await owned_activity(db, activity_id, user.alc_id, lock=True)
    previous = activity.status
    await submit_activity(db, activity, user)
    if previous == ActivityStatus.CORRECTION_REQUIRED:
        admins = (
            await db.scalars(select(User).where(User.role == Role.ADMIN, User.is_active.is_(True)))
        ).all()
        for admin in admins:
            centre_name = user.alc.alc_name if user.alc else "ALC"
            db.add(
                Notification(
                    user_id=admin.id,
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
    return await owned_activity(db, activity.id, user.alc_id)


@router.post("/activities/{activity_id}/evidence", response_model=list[dict], status_code=201)
async def upload_evidence(
    activity_id: uuid.UUID,
    request: Request,
    files: list[UploadFile] = File(...),
    user: User = Depends(require_alc),
    db: AsyncSession = Depends(get_db),
):
    activity = await owned_activity(db, activity_id, user.alc_id)
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
    evidence = await db.scalar(
        select(ActivityEvidence)
        .join(Activity)
        .where(
            ActivityEvidence.id == evidence_id,
            Activity.alc_id == user.alc_id,
            ActivityEvidence.is_active.is_(True),
        )
    )
    if not evidence:
        raise HTTPException(status_code=404, detail="Evidence not found")
    activity = await owned_activity(db, evidence.activity_id, user.alc_id)
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
    user: User = Depends(require_alc),
    db: AsyncSession = Depends(get_db),
):
    evidence = await db.scalar(
        select(ActivityEvidence)
        .join(Activity)
        .where(
            ActivityEvidence.id == evidence_id,
            Activity.alc_id == user.alc_id,
            ActivityEvidence.is_active.is_(True),
        )
    )
    if not evidence:
        raise HTTPException(status_code=404, detail="Evidence not found")
    if settings.storage_backend == "local":
        return {"url": f"{str(request.base_url).rstrip('/')}/api/alc/evidence/{evidence_id}/content", "expires_in": 0}
    return {"url": await storage_service.get_secure_url(evidence.storage_key), "expires_in": settings.s3_presign_seconds}


@router.get("/evidence/{evidence_id}/content")
async def evidence_content(
    evidence_id: uuid.UUID,
    user: User = Depends(require_alc),
    db: AsyncSession = Depends(get_db),
):
    if settings.storage_backend != "local":
        raise HTTPException(status_code=404, detail="Evidence not found")
    evidence = await db.scalar(
        select(ActivityEvidence).join(Activity).where(
            ActivityEvidence.id == evidence_id,
            Activity.alc_id == user.alc_id,
            ActivityEvidence.is_active.is_(True),
        )
    )
    if not evidence:
        raise HTTPException(status_code=404, detail="Evidence not found")
    return Response(await storage_service.get(evidence.storage_key), media_type=evidence.mime_type, headers={"Cache-Control": "private, no-store"})


@router.get("/partners", response_model=list[PartnerOut])
async def partners(user: User = Depends(require_alc), db: AsyncSession = Depends(get_db)):
    return (
        await db.scalars(
            select(Partner).where(Partner.alc_id == user.alc_id).order_by(Partner.partner_name)
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


async def challenge_data(user: User, db: AsyncSession):
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


@router.get("/challenge")
async def challenge(user: User = Depends(require_alc), db: AsyncSession = Depends(get_db)):
    return await challenge_data(user, db)


@router.get("/notifications")
async def notifications(user: User = Depends(require_alc), db: AsyncSession = Depends(get_db)):
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
    user: User = Depends(require_alc),
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


@router.get("/reports/activities.csv")
async def export_activities(user: User = Depends(require_alc), db: AsyncSession = Depends(get_db)):
    items = (
        await db.scalars(
            select(Activity)
            .where(Activity.alc_id == user.alc_id)
            .order_by(desc(Activity.activity_date))
        )
    ).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Activity Number",
            "Date",
            "Type",
            "Ecosystem",
            "Status",
            "Learners",
            "Leads",
            "Admissions",
        ]
    )
    for item in items:
        writer.writerow(
            [
                safe_csv(value)
                for value in [
                    item.activity_number,
                    item.activity_date,
                    item.activity_type,
                    item.ecosystem,
                    item.status.value,
                    item.learners_reached,
                    item.leads_generated,
                    item.admissions_generated,
                ]
            ]
        )
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=alc-activities.csv"},
    )
