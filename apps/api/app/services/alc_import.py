"""Real ALC master import.

Persists ONLY ``ALC Code``, ``ALC Name`` and ``SBU`` from the master source. Any other
column in the source (for example ``DCU``) is ignored and never stored. ``ALC Code`` is the
authoritative unique identifier: existing ALCs are matched by code and updated in place
(name and SBU assignment only), new ALCs are created, and ALCs absent from a later import are
never deleted. The import never creates login accounts and never touches passwords.

Every source ``SBU`` value is mapped dynamically against the existing ``sbus`` table; nothing
is hard-coded. If any required SBU is missing the import is blocked and reports exactly which
SBU records are absent, without writing anything.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import AlcStatus, Role
from app.models import (
    ALC,
    SBU,
    Activity,
    ActivityEvidence,
    ActivityReview,
    ActivityRevision,
    AuditLog,
    ChallengeProgress,
    Notification,
    Partner,
    RefreshToken,
    Task,
    User,
)

# Only these columns are read. DCU (or anything else) is intentionally ignored.
REQUIRED_COLUMNS = ("ALC Code", "ALC Name", "SBU")
CODE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$")


@dataclass
class MasterRow:
    row: int
    alc_code: str
    alc_name: str
    sbu: str


class ImportBlocked(Exception):
    """Raised when an import cannot proceed; carries a structured detail and writes nothing."""

    def __init__(self, detail: dict):
        self.detail = detail
        super().__init__(detail.get("message", "ALC master import blocked"))


# --------------------------------------------------------------------------- #
# Parsing (source of truth: ALC Code as string, leading zeros preserved)
# --------------------------------------------------------------------------- #
def _clean_code(value) -> str:
    """Trim surrounding whitespace and treat the code as a string. Spreadsheet numeric cells
    arrive as floats/ints; render whole numbers without a trailing ``.0`` while leaving any
    text code (including leading zeros) untouched."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).strip()
    if isinstance(value, int):
        return str(value).strip()
    if isinstance(value, float):
        return str(int(value)).strip() if value.is_integer() else str(value).strip()
    return str(value).strip()


def _clean_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value)).strip()
    return str(value).strip()


def _row_is_empty(values) -> bool:
    return not any(v is not None and str(v).strip() != "" for v in values)


def _rows_to_records(header, data_rows) -> list[MasterRow]:
    index = {}
    for position, name in enumerate(header):
        if name is not None:
            index[str(name).strip()] = position
    missing = [c for c in REQUIRED_COLUMNS if c not in index]
    if missing:
        raise ValueError(
            "Source must contain columns "
            f"{', '.join(REQUIRED_COLUMNS)} (missing: {', '.join(missing)})"
        )
    ci, ni, si = index["ALC Code"], index["ALC Name"], index["SBU"]

    def cell(values, pos):
        return values[pos] if pos < len(values) else None

    records: list[MasterRow] = []
    for offset, values in enumerate(data_rows, start=2):  # start=2: row 1 is the header
        if _row_is_empty(values):  # ignore completely empty rows (e.g. trailing blanks)
            continue
        records.append(
            MasterRow(
                row=offset,
                alc_code=_clean_code(cell(values, ci)),
                alc_name=_clean_text(cell(values, ni)),
                sbu=_clean_text(cell(values, si)),
            )
        )
    return records


def parse_csv(content: bytes) -> list[MasterRow]:
    text = content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ValueError("The source file is empty")
    return _rows_to_records(rows[0], rows[1:])


def parse_xlsx(content: bytes) -> list[MasterRow]:
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        worksheet = workbook.active
        rows = list(worksheet.iter_rows(values_only=True))
    finally:
        workbook.close()
    if not rows:
        raise ValueError("The source file is empty")
    return _rows_to_records(list(rows[0]), rows[1:])


def parse_source(filename: str | None, content: bytes) -> list[MasterRow]:
    name = (filename or "").lower()
    if name.endswith(".xlsx"):
        return parse_xlsx(content)
    if name.endswith((".csv", ".txt")):
        return parse_csv(content)
    # Unknown extension: sniff the content, preferring xlsx (zip) then CSV.
    if content[:2] == b"PK":
        return parse_xlsx(content)
    return parse_csv(content)


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #
async def _sbu_by_code(db: AsyncSession) -> dict[str, SBU]:
    sbus = (await db.scalars(select(SBU))).all()
    return {s.code.strip().lower(): s for s in sbus}


async def _existing_alcs(db: AsyncSession, codes) -> dict[str, ALC]:
    codes = [c for c in codes if c]
    if not codes:
        return {}
    alcs = (await db.scalars(select(ALC).where(ALC.alc_code.in_(codes)))).all()
    return {a.alc_code: a for a in alcs}


# --------------------------------------------------------------------------- #
# Validation (never writes)
# --------------------------------------------------------------------------- #
async def validate(db: AsyncSession, records: list[MasterRow]) -> dict:
    sbu_map = await _sbu_by_code(db)
    existing = await _existing_alcs(db, {r.alc_code for r in records})
    seen: dict[str, int] = {}
    results: list[dict] = []
    counts = {"total": len(records), "new": 0, "update": 0, "unchanged": 0, "invalid": 0}
    duplicates: set[str] = set()
    missing_sbus: set[str] = set()

    for r in records:
        reasons: list[str] = []
        if not r.alc_code:
            reasons.append("Blank ALC Code")
        elif not CODE_PATTERN.fullmatch(r.alc_code):
            reasons.append("Invalid ALC Code format")
        if not r.alc_name:
            reasons.append("Blank ALC Name")
        sbu = None
        if not r.sbu:
            reasons.append("Blank SBU")
        else:
            sbu = sbu_map.get(r.sbu.strip().lower())
            if sbu is None:
                reasons.append(f"Unknown SBU '{r.sbu}'")
                missing_sbus.add(r.sbu)
        if r.alc_code:
            if r.alc_code in seen:
                reasons.append(f"Duplicate ALC Code in source (also row {seen[r.alc_code]})")
                duplicates.add(r.alc_code)
            else:
                seen[r.alc_code] = r.row

        if reasons:
            counts["invalid"] += 1
            results.append(
                {
                    "row": r.row,
                    "alc_code": r.alc_code,
                    "alc_name": r.alc_name,
                    "sbu": r.sbu,
                    "action": "invalid",
                    "reason": "; ".join(reasons),
                }
            )
            continue

        alc = existing.get(r.alc_code)
        if alc is None:
            action = "new"
        elif alc.alc_name != r.alc_name or alc.sbu_id != sbu.id:
            action = "update"
        else:
            action = "unchanged"
        counts[action] += 1
        results.append(
            {
                "row": r.row,
                "alc_code": r.alc_code,
                "alc_name": r.alc_name,
                "sbu": sbu.code,
                "action": action,
            }
        )

    return {
        "counts": counts,
        "results": results,
        "invalid": [r for r in results if r["action"] == "invalid"],
        "duplicates": sorted(duplicates),
        "missing_sbus": sorted(missing_sbus),
    }


# --------------------------------------------------------------------------- #
# Import (transactional; aborts without writing if validation fails)
# --------------------------------------------------------------------------- #
async def perform(db: AsyncSession, records: list[MasterRow]) -> dict:
    report = await validate(db, records)
    if report["counts"]["invalid"] > 0:
        raise ImportBlocked(
            {
                "message": (
                    "Import blocked: the source contains invalid rows. "
                    "No changes were written."
                ),
                "counts": report["counts"],
                "missing_sbus": report["missing_sbus"],
                "duplicates": report["duplicates"],
                "invalid": report["invalid"],
            }
        )

    sbu_map = await _sbu_by_code(db)
    existing = await _existing_alcs(db, {r.alc_code for r in records})
    created = updated = unchanged = 0
    for r in records:
        sbu = sbu_map[r.sbu.strip().lower()]
        alc = existing.get(r.alc_code)
        if alc is None:
            db.add(
                ALC(
                    alc_code=r.alc_code,
                    alc_name=r.alc_name,
                    sbu_id=sbu.id,
                    status=AlcStatus.ACTIVE,
                )
            )
            created += 1
        else:
            changed = False
            if alc.alc_name != r.alc_name:
                alc.alc_name = r.alc_name  # update name only; ALC id/users/history preserved
                changed = True
            if alc.sbu_id != sbu.id:
                alc.sbu_id = sbu.id
                changed = True
            if changed:
                updated += 1
            else:
                unchanged += 1
    await db.commit()

    return {
        "total": len(records),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "skipped": 0,
        "failed": 0,
        "counts": report["counts"],
    }


async def counts_by_sbu(db: AsyncSession) -> list[dict]:
    """ALC counts grouped by SBU code, including any ALCs not assigned to an SBU."""
    rows = (
        await db.execute(
            select(SBU.code, func.count(ALC.id))
            .select_from(SBU)
            .outerjoin(ALC, ALC.sbu_id == SBU.id)
            .group_by(SBU.id, SBU.code)
            .order_by(SBU.code)
        )
    ).all()
    result = [{"sbu": code, "alcs": count} for code, count in rows]
    unassigned = await db.scalar(
        select(func.count(ALC.id)).where(ALC.sbu_id.is_(None))
    )
    if unassigned:
        result.append({"sbu": None, "alcs": unassigned})
    return result


async def hard_reset_alc_domain(db: AsyncSession) -> dict[str, int]:
    """DESTRUCTIVE. Remove every ALC-domain row so the master can be loaded fresh:
    all ALCs, all ALC login accounts, all activities (with their evidence, reviews and
    revisions), partners, tasks, challenge progress, and any audit log tied to an ALC or an
    ALC user. ADMIN and SBU accounts and the SBU master are preserved. Rows are deleted in
    foreign-key-safe order (children first) so it works on PostgreSQL and SQLite alike.
    Runs within the caller's transaction; the caller commits."""
    alc_user_ids = select(User.id).where(User.role == Role.ALC)
    removed: dict[str, int] = {}
    removed["evidence"] = (await db.execute(delete(ActivityEvidence))).rowcount
    removed["reviews"] = (await db.execute(delete(ActivityReview))).rowcount
    removed["revisions"] = (await db.execute(delete(ActivityRevision))).rowcount
    removed["activities"] = (await db.execute(delete(Activity))).rowcount
    removed["tasks"] = (await db.execute(delete(Task))).rowcount
    removed["partners"] = (await db.execute(delete(Partner))).rowcount
    removed["challenge_progress"] = (await db.execute(delete(ChallengeProgress))).rowcount
    removed["notifications"] = (
        await db.execute(delete(Notification).where(Notification.user_id.in_(alc_user_ids)))
    ).rowcount
    removed["refresh_tokens"] = (
        await db.execute(delete(RefreshToken).where(RefreshToken.user_id.in_(alc_user_ids)))
    ).rowcount
    removed["audit_logs"] = (
        await db.execute(
            delete(AuditLog).where(
                or_(AuditLog.alc_id.is_not(None), AuditLog.actor_user_id.in_(alc_user_ids))
            )
        )
    ).rowcount
    removed["alc_users"] = (
        await db.execute(delete(User).where(User.role == Role.ALC))
    ).rowcount
    removed["alcs"] = (await db.execute(delete(ALC))).rowcount
    return removed


async def orphan_codes(db: AsyncSession, records: list[MasterRow]) -> list[str]:
    """ALC codes present in the database but absent from the supplied master. These are
    never deleted; they are only reported so a human can decide."""
    source_codes = {r.alc_code for r in records if r.alc_code}
    all_codes = (await db.scalars(select(ALC.alc_code))).all()
    return sorted(c for c in all_codes if c not in source_codes)
