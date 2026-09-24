"""Historical evidence: the files a reviewer actually saw when an activity was submitted.

Every new submission snapshot (``ActivityRevision.snapshot["evidence"]``) records the evidence
that was active at that moment. If the ALC later removes one of those files during a
correction, the evidence row is only hidden (``is_active=False``) and its stored file is kept,
so review history stays verifiable. Such evidence is listed as "removed" on the activity and
can still be opened by anyone allowed to see the activity (the same ALC/SBU/DCU/Admin scope).

Snapshots written before this change have no ``evidence`` key. Old history is never
rewritten; for those submissions, evidence uploaded before the submission time is treated as
part of it (``recorded=False`` tells the UI the list was inferred, not recorded).
"""
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity, ActivityEvidence, ActivityRevision


def snapshot_evidence(activity: Activity) -> list[dict[str, Any]]:
    """Evidence metadata stored in a new revision snapshot (active evidence only)."""
    return [
        {
            "id": str(item.id),
            "original_filename": item.original_filename,
            "mime_type": item.mime_type,
            "file_size": item.file_size,
        }
        for item in activity.evidence
        if item.is_active
    ]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def submissions_including(
    evidence: ActivityEvidence, revisions: list[ActivityRevision]
) -> tuple[list[int], bool]:
    """Revision numbers whose submission included ``evidence``, and whether that was recorded.

    Returns ``([], False)`` for evidence no reviewer has ever been sent.
    """
    numbers: list[int] = []
    recorded = False
    for revision in revisions:
        listed = (revision.snapshot or {}).get("evidence")
        if listed is None:  # snapshot from before evidence was recorded
            if evidence.uploaded_at and _aware(evidence.uploaded_at) <= _aware(
                revision.created_at
            ):
                numbers.append(revision.revision_number)
        elif any(entry.get("id") == str(evidence.id) for entry in listed):
            numbers.append(revision.revision_number)
            recorded = True
    return numbers, recorded


def removed_evidence(activity: Activity) -> list[dict[str, Any]]:
    """Evidence hidden from the current list that was part of an earlier submission."""
    items = []
    for item in activity.evidence:
        if item.is_active:
            continue
        numbers, recorded = submissions_including(item, activity.revisions)
        if numbers:
            items.append(
                {
                    "id": item.id,
                    "original_filename": item.original_filename,
                    "mime_type": item.mime_type,
                    "file_size": item.file_size,
                    "uploaded_at": item.uploaded_at,
                    "submitted_in": numbers,
                    "recorded": recorded,
                }
            )
    return items


async def is_historical(db: AsyncSession, evidence: ActivityEvidence) -> bool:
    """True when retired ``evidence`` belongs to an earlier submission (so it stays viewable)."""
    revisions = list(
        await db.scalars(
            select(ActivityRevision).where(ActivityRevision.activity_id == evidence.activity_id)
        )
    )
    return bool(submissions_including(evidence, revisions)[0])


async def evidence_viewable(db: AsyncSession, evidence: ActivityEvidence) -> bool:
    """Current evidence, or removed evidence that a reviewer saw in an earlier submission."""
    return evidence.is_active or await is_historical(db, evidence)