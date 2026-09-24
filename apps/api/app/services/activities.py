import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.enums import ActivityStatus, ReviewAction
from app.models import Activity, ActivityReview, ActivityRevision, Notification, User
from app.services.evidence_history import snapshot_evidence

EDITABLE_STATUSES = {ActivityStatus.DRAFT, ActivityStatus.CORRECTION_REQUIRED}
REVIEWABLE_STATUSES = {
    ActivityStatus.SUBMITTED,
    ActivityStatus.RESUBMITTED,
    ActivityStatus.UNDER_REVIEW,
}
# Final decisions that an authorised reviewer (DCU or Admin) may later change.
FINAL_STATUSES = {ActivityStatus.VERIFIED, ActivityStatus.REJECTED}
ACTION_RESULT = {
    ReviewAction.VERIFY: ActivityStatus.VERIFIED,
    ReviewAction.REQUEST_CORRECTION: ActivityStatus.CORRECTION_REQUIRED,
    ReviewAction.REJECT: ActivityStatus.REJECTED,
}


def activity_snapshot(activity: Activity) -> dict:
    return {
        "activity_type": activity.activity_type,
        "ecosystem": activity.ecosystem,
        "collaboration_type": activity.collaboration_type,
        "activity_date": activity.activity_date.isoformat(),
        "location": activity.location,
        "learners_reached": activity.learners_reached,
        "leads_generated": activity.leads_generated,
        "admissions_generated": activity.admissions_generated,
        "description": activity.description,
                "outcome": activity.outcome,
        # Evidence the reviewer is sent with this submission (kept for review history).
        "evidence": snapshot_evidence(activity),
    }
    


async def owned_activity(
    db: AsyncSession, activity_id: uuid.UUID, alc_id: uuid.UUID, lock: bool = False
) -> Activity:
    query = (
        select(Activity)
        .options(
            selectinload(Activity.partner),
            selectinload(Activity.evidence),
            selectinload(Activity.reviews),
            selectinload(Activity.revisions),
        )
        .where(Activity.id == activity_id, Activity.alc_id == alc_id)
    )
    if lock:
        query = query.with_for_update(of=Activity)
    result = await db.scalar(query)
    if not result:
        raise HTTPException(status_code=404, detail="Activity not found")
    return result


async def admin_activity(db: AsyncSession, activity_id: uuid.UUID, lock: bool = False) -> Activity:
    query = (
        select(Activity)
        .options(
            selectinload(Activity.partner),
            selectinload(Activity.evidence),
            selectinload(Activity.reviews),
            selectinload(Activity.revisions),
        )
        # Admin works on submitted-workflow data; a DRAFT is private to its ALC.
        .where(Activity.id == activity_id, Activity.status != ActivityStatus.DRAFT)
    )
    if lock:
        query = query.with_for_update(of=Activity)
    result = await db.scalar(query)
    if not result:
        raise HTTPException(status_code=404, detail="Activity not found")
    return result


async def submit_activity(db: AsyncSession, activity: Activity, user: User) -> Activity:
    if activity.status not in EDITABLE_STATUSES:
        raise HTTPException(
            status_code=409, detail="Activity cannot be submitted in its current status"
        )
    if not any(item.is_active for item in activity.evidence):
        raise HTTPException(status_code=422, detail="At least one evidence file is required")
    previous = activity.status
    activity.status = (
        ActivityStatus.RESUBMITTED
        if previous == ActivityStatus.CORRECTION_REQUIRED
        else ActivityStatus.SUBMITTED
    )
    activity.submitted_at = datetime.now(timezone.utc)
    revision_number = (
        await db.scalar(
            select(func.count(ActivityRevision.id)).where(
                ActivityRevision.activity_id == activity.id
            )
        )
        or 0
    ) + 1
    db.add(
        ActivityRevision(
            activity_id=activity.id,
            revision_number=revision_number,
            changed_by=user.id,
            change_summary="Resubmitted after correction"
            if previous == ActivityStatus.CORRECTION_REQUIRED
            else "Initial submission",
            snapshot=activity_snapshot(activity),
        )
    )
    return activity


async def review_activity(
    db: AsyncSession,
    activity: Activity,
    reviewer: User,
    action: ReviewAction,
    remark: str | None,
    change_decision: bool = False,
) -> Activity:
    """Apply a review decision and append it to the activity's review history.

    Normal review acts on an activity awaiting review; a reason is required to request a
    correction or reject. ``change_decision`` instead overrides an earlier *final* decision
    (VERIFIED or REJECTED) — callers must restrict it to roles allowed to do so — and always
    requires a reason, even when the new decision is Verify. Either way a new
    ``ActivityReview`` row is appended: earlier reviews are never edited or deleted, and
    dashboards/reports follow the activity's new current status.
    """
    has_remark = bool(remark and remark.strip())
    if change_decision:
        if activity.status not in FINAL_STATUSES:
            raise HTTPException(
                status_code=409,
                detail="Only a verified or rejected activity's decision can be changed",
            )
        if not has_remark:
            raise HTTPException(
                status_code=422, detail="A reason is required to change a decision"
            )
        if ACTION_RESULT[action] == activity.status:
            raise HTTPException(
                status_code=409, detail=f"Activity is already {activity.status.value}"
            )
    else:
        if activity.status not in REVIEWABLE_STATUSES:
            raise HTTPException(status_code=409, detail="Activity is not awaiting review")
        if action in {ReviewAction.REQUEST_CORRECTION, ReviewAction.REJECT} and not has_remark:
            raise HTTPException(status_code=422, detail="A review reason is required")
    previous = activity.status
    activity.status = ACTION_RESULT[action]
    if action == ReviewAction.VERIFY:
        activity.verified_at = datetime.now(timezone.utc)
        activity.verified_by = reviewer.id
    else:
        # The activity is no longer verified; who verified it earlier stays in the history.
        activity.verified_at = None
        activity.verified_by = None
    db.add(
        ActivityReview(
            activity_id=activity.id,
            reviewer_id=reviewer.id,
            reviewer_role=reviewer.role.value,
            previous_status=previous,
            new_status=activity.status,
            action=action,
            remark=remark.strip() if remark else None,
            is_decision_change=change_decision,
        )
    )
    alc_users = (
        await db.scalars(
            select(User).where(User.alc_id == activity.alc_id, User.is_active.is_(True))
        )
    ).all()
    title = {
        ReviewAction.VERIFY: "Activity verified",
        ReviewAction.REQUEST_CORRECTION: "Correction requested",
        ReviewAction.REJECT: "Activity rejected",
    }[action]
    if change_decision:
        title = f"Review decision changed: {title.lower()}"
    for recipient in alc_users:
        db.add(
            Notification(
                user_id=recipient.id,
                title=title,
                message=f"{activity.activity_number}: {remark or title}",
                type=action.value,
                entity_type="activity",
                entity_id=str(activity.id),
            )
        )
    return activity
