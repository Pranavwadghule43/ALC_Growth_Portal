"""Evidence-backed participation. Run share locks + submission locks serialize writers.

Legacy Completion rows remain stable membership/attribution snapshots; their Boolean is
never proof of completion. Only this module's VERIFIED state can contribute to scores.
"""

import asyncio
import hashlib
import math
import uuid
from pathlib import Path

import structlog
from fastapi import HTTPException, UploadFile
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.enums import Role
from app.models import (
    ALC,
    SBU,
    utcnow,
)
from app.models import (
    YearlyActivityCompletion as Participation,
)
from app.models import (
    YearlyActivityDCURun as Run,
)
from app.models import (
    YearlyActivityDefinition as Definition,
)
from app.models import (
    YearlyActivityEvidence as Evidence,
)
from app.models import (
    YearlyActivityReview as Review,
)
from app.models import (
    YearlyActivitySubmission as Submission,
)
from app.services.audit import record_audit
from app.services.growth_challenge import current_date
from app.services.scope import alc_scope
from app.services.yearly_activities import definition_scope
from app.storage import storage_service
from app.yearly_schemas import YearlyReviewIn

logger = structlog.get_logger(__name__)

EDITABLE = {"DRAFT", "CORRECTION_REQUIRED"}
PENDING = {"SUBMITTED", "RESUBMITTED"}
STATES = EDITABLE | PENDING | {"VERIFIED", "REJECTED"}
MAX_HISTORY_FILES = 50
UPLOAD_TIMEOUT = 45
MIMES = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/webp": {".webp"},
    "application/pdf": {".pdf"},
    "video/mp4": {".mp4"},
    "video/webm": {".webm"},
}


def detect_type(body: bytes):
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return "image/webp"
    if body.startswith(b"%PDF-"):
        return "application/pdf"
    if len(body) >= 24 and body[4:8] == b"ftyp":
        box_size = int.from_bytes(body[:4], "big")
        brands = {body[i : i + 4] for i in range(8, min(box_size, len(body), 64), 4)}
        if 16 <= box_size <= len(body) and brands & {b"isom", b"iso2", b"mp41", b"mp42", b"avc1"}:
            return "video/mp4"
    if body.startswith(b"\x1a\x45\xdf\xa3") and b"webm" in body[:4096]:
        return "video/webm"
    return None


def scope(user):
    clause = alc_scope(user, Participation.alc_id)
    if user.role == Role.DCU:
        clause = clause & (Run.dcu_id == user.dcu_id)
    return clause & definition_scope(user)


def context_query(user):
    return (
        select(Submission, Participation, Run, Definition)
        .join(Participation, Submission.participation_id == Participation.id)
        .join(Run, Participation.yearly_activity_dcu_run_id == Run.id)
        .join(Definition, Participation.yearly_activity_id == Definition.id)
        .where(scope(user))
    )


async def context(db, user, submission_id, *, lock=False):
    row = (await db.execute(context_query(user).where(Submission.id == submission_id))).first()
    if not row:
        raise HTTPException(404, "Submission not found")
    if lock:
        await db.scalar(
            select(Run)
            .where(Run.id == row[2].id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        row = (
            await db.execute(
                context_query(user)
                .where(Submission.id == submission_id)
                .with_for_update(of=Submission)
                .execution_options(populate_existing=True)
            )
        ).first()
    return row


def editable(user, sub, run):
    if user.role != Role.ALC or sub.status not in EDITABLE or run.status == "FINALIZED":
        raise HTTPException(409, "Submission is locked")
    if sub.status == "DRAFT" and (
        run.status not in {"ACTIVE", "MARKING"}
        or not run.scheduled_start_date <= current_date() <= run.scheduled_end_date
    ):
        raise HTTPException(409, "The initial submission window is closed")
    if run.status not in {"ACTIVE", "MARKING"}:
        raise HTTPException(409, "Activity is not open")


def apply_input(sub, run, data):
    if data.completion_date and not (
        run.scheduled_start_date <= data.completion_date <= run.scheduled_end_date
        and data.completion_date <= current_date()
    ):
        raise HTTPException(422, "Completion date must be within the schedule and not future")
    sub.completion_date, sub.remarks = data.completion_date, data.remarks


def evidence_out(row):
    return {
        "id": row.id,
        "original_filename": row.original_filename,
        "mime_type": row.mime_type,
        "file_size": row.file_size,
        "sha256": row.sha256,
        "uploaded_at": row.uploaded_at,
        "is_active": row.is_active,
        "submitted_at": row.submitted_at,
    }


async def detail(db, user, submission_id):
    sub, member, run, definition = await context(db, user, submission_id)
    if sub.revision == 0 and user.role != Role.ALC:
        raise HTTPException(404, "Submission has not been submitted")
    evidence = (
        await db.scalars(
            select(Evidence)
            .where(Evidence.submission_id == sub.id)
            .order_by(Evidence.uploaded_at, Evidence.id)
        )
    ).all()
    if user.role != Role.ALC:
        evidence = [e for e in evidence if e.submitted_at]
    reviews = (
        await db.scalars(
            select(Review)
            .where(Review.submission_id == sub.id)
            .order_by(Review.reviewed_at, Review.id)
        )
    ).all()
    alc = await db.get(ALC, member.alc_id)
    sbu = await db.get(SBU, member.snapshot_sbu_id)
    return {
        "id": sub.id,
        "participation_id": member.id,
        "yearly_activity_id": definition.id,
        "run_id": run.id,
        "activity_name": definition.name,
        "calendar_year": definition.calendar_year,
        "alc_id": alc.id,
        "alc_code": alc.alc_code,
        "alc_name": alc.alc_name,
        "sbu_name": sbu.name,
        "status": sub.status,
        "revision": sub.revision,
        "completion_date": sub.completion_date,
        "remarks": sub.remarks,
        "submitted_at": sub.submitted_at,
        "resubmitted_at": sub.resubmitted_at,
        "verified_at": sub.verified_at,
        "updated_at": sub.updated_at,
        "run_status": run.status,
        "scheduled_start_date": run.scheduled_start_date,
        "scheduled_end_date": run.scheduled_end_date,
        "upload_limits": {
            "max_files": settings.max_upload_files,
            "max_bytes": settings.max_upload_bytes,
        },
        "can_edit": user.role == Role.ALC
        and sub.status in EDITABLE
        and run.status in {"ACTIVE", "MARKING"}
        and (
            sub.status == "CORRECTION_REQUIRED"
            or run.scheduled_start_date <= current_date() <= run.scheduled_end_date
        ),
        "can_review": user.role == Role.DCU
        and sub.status in PENDING
        and run.status in {"ACTIVE", "MARKING"},
        "evidence": [evidence_out(e) for e in evidence],
        "reviews": [
            {
                "id": r.id,
                "revision": r.revision,
                "reviewer_role": r.reviewer_role,
                "previous_status": r.previous_status,
                "decision": r.decision,
                "remarks": r.remarks,
                "reviewed_at": r.reviewed_at,
                "snapshot": r.snapshot,
            }
            for r in reviews
        ],
    }


async def create(db, user, run_id, data, request):
    run = await db.scalar(
        select(Run)
        .join(Definition, Run.yearly_activity_id == Definition.id)
        .where(Run.id == run_id)
        .where(definition_scope(user))
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    member = await db.scalar(
        select(Participation).where(
            Participation.yearly_activity_dcu_run_id == run_id,
            Participation.alc_id == user.alc_id,
            alc_scope(user, Participation.alc_id),
        )
    )
    if not run or not member:
        raise HTTPException(404, "Applicable activity not found")
    sub = Submission(participation_id=member.id, created_by=user.id, status="DRAFT")
    editable(user, sub, run)
    apply_input(sub, run, data)
    db.add(sub)
    try:
        await db.flush()
        await record_audit(
            db,
            "yearly_activity_submission_created",
            "yearly_activity_submission",
            sub.id,
            user,
            request,
            {"run_id": str(run_id)},
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "A submission already exists for this activity") from None
    return await detail(db, user, sub.id)


async def save(db, user, submission_id, data, request):
    sub, _, run, _ = await context(db, user, submission_id, lock=True)
    editable(user, sub, run)
    apply_input(sub, run, data)
    await record_audit(
        db,
        "yearly_activity_submission_edited",
        "yearly_activity_submission",
        sub.id,
        user,
        request,
        {"status": sub.status},
    )
    await db.commit()
    return await detail(db, user, sub.id)


async def upload(db, user, submission_id, files: list[UploadFile], request):
    sub, member, run, _ = await context(db, user, submission_id, lock=True)
    editable(user, sub, run)
    if len(files) != 1:
        raise HTTPException(422, "Upload one evidence file per request")
    total, active = (
        await db.execute(
            select(
                func.count(Evidence.id), func.sum(case((Evidence.is_active.is_(True), 1), else_=0))
            ).where(Evidence.submission_id == sub.id)
        )
    ).one()
    if total >= MAX_HISTORY_FILES or int(active or 0) >= settings.max_upload_files:
        raise HTTPException(422, "Evidence file count limit reached")
    file = files[0]
    try:
        body = await asyncio.wait_for(file.read(settings.max_upload_bytes + 1), UPLOAD_TIMEOUT)
    except TimeoutError as exc:
        raise HTTPException(408, "Evidence read timed out; please retry") from exc
    finally:
        await file.close()
    if not body or len(body) > settings.max_upload_bytes:
        raise HTTPException(413, "Evidence is empty or exceeds the configured file limit")
    detected, extension = detect_type(body), Path(file.filename or "").suffix.lower()
    if detected not in MIMES or extension not in MIMES[detected] or file.content_type != detected:
        raise HTTPException(415, "Unsupported or mismatched evidence type/signature")
    evidence_id = uuid.uuid4()
    key = f"yearly-evidence/{member.alc_id}/{sub.id}/{evidence_id}{extension}"
    stored = False
    try:
        await asyncio.wait_for(storage_service.upload(key, body, detected), UPLOAD_TIMEOUT)
        stored = True
        row = Evidence(
            id=evidence_id,
            submission_id=sub.id,
            storage_key=key,
            original_filename=(file.filename or "evidence")[:255],
            mime_type=detected,
            file_size=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            uploaded_by=user.id,
        )
        db.add(row)
        await record_audit(
            db,
            "yearly_activity_evidence_uploaded",
            "yearly_activity_evidence",
            row.id,
            user,
            request,
            {"submission_id": str(sub.id)},
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        if stored:
            try:
                await asyncio.wait_for(storage_service.delete(key), 5)
            except Exception as cleanup_error:
                logger.error(
                    "yearly_evidence_orphan_cleanup_failed",
                    evidence_id=str(evidence_id),
                    error_type=type(cleanup_error).__name__,
                )
        raise HTTPException(503, "Evidence upload failed; please retry") from exc
    return evidence_out(row)


async def remove(db, user, evidence_id, request):
    evidence = await db.get(Evidence, evidence_id)
    if not evidence:
        raise HTTPException(404, "Evidence not found")
    sub, _, run, _ = await context(db, user, evidence.submission_id, lock=True)
    editable(user, sub, run)
    evidence.is_active = False  # retain prior review proof and object, never overwrite history
    await record_audit(
        db,
        "yearly_activity_evidence_removed",
        "yearly_activity_evidence",
        evidence.id,
        user,
        request,
        {"submission_id": str(sub.id)},
    )
    await db.commit()
    return {"message": "Evidence removed from current submission"}


async def submit(db, user, submission_id, request):
    sub, _, run, _ = await context(db, user, submission_id, lock=True)
    editable(user, sub, run)
    if not sub.completion_date or len(sub.remarks.strip()) < 10:
        raise HTTPException(422, "Completion date and at least ten characters of details required")
    if not await db.scalar(
        select(Evidence.id)
        .where(Evidence.submission_id == sub.id, Evidence.is_active.is_(True))
        .limit(1)
    ):
        raise HTTPException(422, "At least one valid evidence file is required")
    previous = sub.status
    sub.status = "RESUBMITTED" if previous == "CORRECTION_REQUIRED" else "SUBMITTED"
    sub.revision += 1
    now = utcnow()
    if sub.submitted_at is None:
        sub.submitted_at = now
    else:
        sub.resubmitted_at = now
    await db.execute(
        update(Evidence)
        .where(
            Evidence.submission_id == sub.id,
            Evidence.is_active.is_(True),
            Evidence.submitted_at.is_(None),
        )
        .values(submitted_at=now)
    )
    await record_audit(
        db,
        "yearly_activity_resubmitted"
        if previous == "CORRECTION_REQUIRED"
        else "yearly_activity_submitted",
        "yearly_activity_submission",
        sub.id,
        user,
        request,
        {"previous_status": previous, "status": sub.status, "revision": sub.revision},
    )
    await db.commit()
    return await detail(db, user, sub.id)


async def review(db, user, submission_id, data: YearlyReviewIn, request):
    sub, _, run, _ = await context(db, user, submission_id, lock=True)
    if run.status not in {"ACTIVE", "MARKING"} or sub.status not in PENDING:
        raise HTTPException(409, "Submission is not awaiting review or activity is finalized")
    if sub.revision != data.revision:
        raise HTTPException(409, "Submission version changed; reload before reviewing")
    files = (
        await db.scalars(
            select(Evidence).where(
                Evidence.submission_id == sub.id,
                Evidence.is_active.is_(True),
                Evidence.submitted_at.is_not(None),
            )
        )
    ).all()
    if not files:
        raise HTTPException(409, "Submitted evidence is required before review")
    previous = sub.status
    target = {
        "VERIFY": "VERIFIED",
        "REQUEST_CORRECTION": "CORRECTION_REQUIRED",
        "REJECT": "REJECTED",
    }[data.decision]
    db.add(
        Review(
            submission_id=sub.id,
            revision=sub.revision,
            reviewer_id=user.id,
            previous_status=previous,
            decision=data.decision,
            remarks=data.remarks,
            snapshot={
                "completion_date": str(sub.completion_date),
                "remarks": sub.remarks,
                "submitted_at": str(sub.resubmitted_at or sub.submitted_at),
                "evidence": [
                    {
                        **evidence_out(e),
                        "id": str(e.id),
                        "uploaded_at": str(e.uploaded_at),
                        "submitted_at": str(e.submitted_at),
                    }
                    for e in files
                ],
            },
        )
    )
    sub.status, sub.verified_at = target, utcnow() if target == "VERIFIED" else None
    event = {
        "VERIFIED": "verified",
        "CORRECTION_REQUIRED": "correction_requested",
        "REJECTED": "rejected",
    }[target]
    await record_audit(
        db,
        f"yearly_activity_{event}",
        "yearly_activity_submission",
        sub.id,
        user,
        request,
        {"previous_status": previous, "status": target, "revision": sub.revision},
    )
    await db.commit()
    return await detail(db, user, sub.id)


async def scoped_evidence(db, user, evidence_id):
    row = await db.get(Evidence, evidence_id)
    if not row:
        raise HTTPException(404, "Evidence not found")
    await context(db, user, row.submission_id)
    if user.role != Role.ALC and row.submitted_at is None:
        raise HTTPException(404, "Evidence not found")
    return row


async def listing(
    db,
    user,
    *,
    year,
    run_id=None,
    activity_id=None,
    sbu_id=None,
    status=None,
    q=None,
    page=1,
    page_size=25,
):
    clauses = [Definition.calendar_year == year, scope(user)]
    if run_id:
        clauses.append(Run.id == run_id)
    if activity_id:
        clauses.append(Definition.id == activity_id)
    if sbu_id:
        clauses.append(Participation.snapshot_sbu_id == sbu_id)
    if status:
        clauses.append(Submission.status == status)
    if q:
        clauses.append(or_(ALC.alc_name.ilike(f"%{q}%"), ALC.alc_code.ilike(f"%{q}%")))
    base = (
        select(
            Participation.id,
            Participation.alc_id,
            ALC.alc_code,
            ALC.alc_name,
            SBU.name.label("sbu_name"),
            SBU.id.label("sbu_id"),
            Run.id.label("run_id"),
            Definition.id.label("activity_id"),
            Definition.name.label("activity_name"),
            Run.scheduled_end_date.label("deadline"),
            Submission.id.label("submission_id"),
            func.coalesce(Submission.status, "NOT_SUBMITTED").label("status"),
            Submission.completion_date,
            Submission.verified_at,
        )
        .join(Run, Participation.yearly_activity_dcu_run_id == Run.id)
        .join(Definition, Participation.yearly_activity_id == Definition.id)
        .join(ALC, Participation.alc_id == ALC.id)
        .join(SBU, Participation.snapshot_sbu_id == SBU.id)
        .outerjoin(Submission, Submission.participation_id == Participation.id)
        .where(*clauses)
    )
    population = base.subquery("submission_population")
    summary = {
        status: int(count)
        for status, count in await db.execute(
            select(population.c.status, func.count()).group_by(population.c.status)
        )
    }
    # Each participation has at most one submission, so the grouped population is a
    # lossless partition of the filtered rows and supplies both the summary and total.
    total = sum(summary.values())
    rows = (
        (
            await db.execute(
                base.order_by(Definition.activity_code, ALC.alc_code)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .mappings()
        .all()
    )
    ids = {r["alc_id"] for r in rows}
    annual = {}
    if ids:
        for alc_id, state, count in await db.execute(
            select(Participation.alc_id, Submission.status, func.count(Submission.id))
            .join(Submission, Submission.participation_id == Participation.id)
            .join(Definition, Participation.yearly_activity_id == Definition.id)
            .where(
                Participation.alc_id.in_(ids),
                Definition.calendar_year == year,
                definition_scope(user),
                Definition.rcu_id.in_(
                    select(Definition.rcu_id)
                    .join(Run, Run.yearly_activity_id == Definition.id)
                    .where(Run.id == run_id)
                )
                if run_id
                else True,
            )
            .group_by(Participation.alc_id, Submission.status)
        ):
            annual.setdefault(alc_id, {})[state] = int(count)
    items = []
    for row in rows:
        counts = annual.get(row["alc_id"], {})
        items.append(
            {
                **dict(row),
                "yearly_verified_count": counts.get("VERIFIED", 0),
                "yearly_pending_count": sum(counts.get(s, 0) for s in PENDING),
                "yearly_correction_count": counts.get("CORRECTION_REQUIRED", 0),
            }
        )
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": math.ceil(total / page_size),
        "summary": summary,
    }
