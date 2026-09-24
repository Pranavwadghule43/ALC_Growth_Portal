"""RCU Pune ALC master: canonical parsing, validation rules, safe idempotent import,
reassignment without history loss, and preservation of the Nashik mapping.

The ``pune`` fixture reproduces the current production state: SBU 4 / 6 / 7 plus an unplaced
``SBU PN1``, the RCU Pune / DCU hierarchy from ``ensure_hierarchy`` (SBU 4 / 6 / 7 under DCU
Nashik), and the 199-ALC Nashik master (``data/ALC-MASTER.csv``) loaded by the existing
legacy importer."""

import csv
import io
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.auth import hash_password
from app.enums import ActivityStatus, AlcStatus, ReviewAction, Role
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
    Partner,
    User,
)
from app.services import alc_import, master_import
from app.services.hierarchy import ensure_hierarchy
from scripts import validate_alc_master

MASTER = Path(__file__).resolve().parents[3] / "data" / "ALC-MASTER.csv"
HEADER = "RCU,DCU,SBU,ALC Code,ALC Name,Active"
NASHIK = {"SBU 4": 71, "SBU 6": 58, "SBU 7": 70}
JAYESH = "57210164"  # Jayesh Computers, SBU 4


def csv_bytes(*rows: str) -> bytes:
    return ("\n".join(rows) + "\n").encode("utf-8")


def parse(*rows: str):
    return master_import.parse_source("m.csv", csv_bytes(HEADER, *rows))


def nashik_canonical(active: str = "Yes") -> bytes:
    """The real Nashik master in canonical form (every row RCU Pune / DCU Nashik)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(HEADER.split(","))
    with MASTER.open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            writer.writerow(
                ["RCU Pune", "DCU Nashik", row["SBU"], row["ALC Code"], row["ALC Name"], active]
            )
    return out.getvalue().encode("utf-8")


async def sbu_counts(session) -> dict[str, int]:
    rows = (
        await session.execute(
            select(SBU.code, func.count(ALC.id)).join(ALC, ALC.sbu_id == SBU.id).group_by(SBU.code)
        )
    ).all()
    return dict(rows)


async def snapshot(session) -> list[tuple]:
    alcs = (await session.scalars(select(ALC).order_by(ALC.alc_code))).all()
    return [(a.id, a.alc_code, a.alc_name, a.sbu_id, a.status, a.updated_at) for a in alcs]


async def count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


@pytest.fixture
async def hierarchy_only(session):
    session.add_all(
        [
            SBU(code="SBU 4", name="Strategic Business Unit 4"),
            SBU(code="SBU 6", name="Strategic Business Unit 6"),
            SBU(code="SBU 7", name="Strategic Business Unit 7"),
            SBU(code="SBU PN1", name="Pune North SBU 1"),
        ]
    )
    await session.flush()
    await ensure_hierarchy(session)
    await session.commit()
    return session


@pytest.fixture
async def pune(hierarchy_only):
    session = hierarchy_only
    records = alc_import.parse_source(MASTER.name, MASTER.read_bytes())
    await alc_import.perform(session, records)
    return session


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def test_parse_requires_canonical_columns():
    with pytest.raises(master_import.MasterFileError) as exc:
        master_import.parse_source("m.csv", csv_bytes("ALC Code,ALC Name,SBU", "1,Two,SBU 4"))
    assert "RCU" in str(exc.value) and "Active" in str(exc.value)


def test_parse_rejects_duplicate_header():
    with pytest.raises(master_import.MasterFileError):
        master_import.parse_source("m.csv", csv_bytes(HEADER + ",SBU", "a,b,c,d,e,f,g"))


def test_parse_header_case_insensitive_code_stays_string():
    content = csv_bytes(
        " rcu ,dcu,Sbu,ALC  CODE,alc name,ACTIVE",
        "RCU Pune,DCU Nashik,SBU 4,  00012345 ,  Zero   Centre ,Yes",
        ",,,,,",
    )
    (record,) = master_import.parse_source("m.csv", content)
    assert record.alc_code == "00012345" and isinstance(record.alc_code, str)
    assert record.alc_name == "Zero Centre"
    assert record.row == 2


def test_parse_xlsx_numeric_code_is_string_with_warning():
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(HEADER.split(","))
    sheet.append(["RCU Pune", "DCU Nashik", "SBU 4", 57210164, "Jayesh Computers", "Yes"])
    sheet.append(["RCU Pune", "DCU Nashik", "SBU 4", "00057", "Text Code Centre", "Yes"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    numeric, text = master_import.parse_source("m.xlsx", buffer.getvalue())
    assert numeric.alc_code == "57210164" and numeric.warnings
    assert text.alc_code == "00057" and not text.warnings


# --------------------------------------------------------------------------- #
# Validation rules (never writes)
# --------------------------------------------------------------------------- #
def errors_by_row(report) -> dict[int, str]:
    return {row["row"]: "; ".join(row["errors"]) for row in report["invalid"]}


@pytest.mark.asyncio
async def test_validate_missing_fields(pune):
    report = await master_import.validate(
        pune,
        parse(
            ",DCU Nashik,SBU 4,90000001,No RCU,Yes",
            "RCU Pune,DCU Nashik,SBU 4,,No Code,Yes",
            "RCU Pune,DCU Nashik,SBU 4,90000003,,Yes",
            "RCU Pune,DCU Nashik,SBU 4,90000004,No Active,",
            "RCU Pune,,,90000005,No DCU or SBU,Yes",
        ),
    )
    errors = errors_by_row(report)
    assert "Missing RCU" in errors[2]
    assert "Missing ALC Code" in errors[3]
    assert "Missing ALC Name" in errors[4]
    assert "Missing Active" in errors[5]
    assert "Missing DCU" in errors[6] and "Missing SBU" in errors[6]
    assert report["valid"] is False and report["counts"]["invalid"] == 5


@pytest.mark.asyncio
async def test_validate_duplicate_codes_and_duplicate_rows(pune):
    report = await master_import.validate(
        pune,
        parse(
            "RCU Pune,DCU Nashik,SBU 4,90000001,First,Yes",
            "RCU Pune,DCU Nashik,SBU 4,90000001,Different Name,Yes",
            "RCU Pune,DCU Nashik,SBU 6,90000002,Twin,Yes",
            "RCU Pune,DCU Nashik,SBU 6,90000002,Twin,Yes",
            "RCU Pune,DCU Nashik,SBU 4,ab12345,Lower,Yes",
            "RCU Pune,DCU Nashik,SBU 4,AB12345,Upper,Yes",
        ),
    )
    errors = errors_by_row(report)
    assert "Duplicate ALC Code '90000001' (also row 3)" in errors[2]
    assert "Duplicate ALC Code '90000001' (also row 2)" in errors[3]
    assert 4 not in errors  # the first of two identical rows is fine on its own...
    assert "Duplicate row (identical to row 4)" in errors[5]  # ...the repeat is not
    assert "Duplicate ALC Code" in errors[7]  # codes are compared case-insensitively


@pytest.mark.asyncio
async def test_validate_conflicting_mappings_in_file(pune):
    report = await master_import.validate(
        pune,
        parse(
            "RCU Pune,DCU Pune North,SBU PN1,90000001,One,Yes",
            "RCU Pune,DCU Pune South,SBU PN1,90000002,Two,Yes",
            "RCU Pune,DCU_PUNE_NORTH,SBU PN1,90000003,Alias Spelling,Yes",
        ),
    )
    errors = errors_by_row(report)
    assert all("Conflicting SBU/DCU mapping" in errors[row] for row in (2, 3, 4))


@pytest.mark.asyncio
async def test_validate_accepts_hierarchy_spellings(pune):
    report = await master_import.validate(
        pune,
        parse(
            "RCU Pune,DCU Pune North,SBU PN1,90000001,One,Yes",
            "RCU_PUNE,DCU_PUNE_NORTH,sbu pn1,90000002,Two,y",
            "Pune,Pune North,SBU PN1,90000003,Three,ACTIVE",
        ),
    )
    assert report["valid"], report["invalid"]
    assert report["sbu_links"] == [{"sbu": "SBU PN1", "dcu": "DCU_PUNE_NORTH"}]


@pytest.mark.asyncio
async def test_validate_unknown_hierarchy_values(pune):
    report = await master_import.validate(
        pune,
        parse(
            "RCU Mumbai,DCU Nashik,SBU 4,90000001,Unknown RCU,Yes",
            "RCU Pune,DCU Satara,SBU 4,90000002,Unknown DCU,Yes",
            "RCU Pune,DCU Nashik,SBU 99,90000003,Unknown SBU,Yes",
        ),
    )
    errors = errors_by_row(report)
    assert "Unknown RCU 'RCU Mumbai'" in errors[2]
    assert "Unknown DCU 'DCU Satara'" in errors[3]
    assert "Unknown SBU 'SBU 99'" in errors[4]


@pytest.mark.asyncio
async def test_validate_dcu_rcu_and_sbu_dcu_conflicts_with_database(pune):
    other = RCU(code="RCU_OTHER", name="RCU Other")
    pune.add(other)
    await pune.flush()
    pune.add(DCU(code="DCU_ELSEWHERE", name="DCU Elsewhere", rcu_id=other.id))
    await pune.commit()
    report = await master_import.validate(
        pune,
        parse(
            "RCU Pune,DCU Elsewhere,SBU PN1,90000001,Wrong RCU,Yes",
            # SBU 4 is already under DCU Nashik: the file may not re-parent it.
            "RCU Pune,DCU Pune South,SBU 4,90000002,Wrong DCU,Yes",
        ),
    )
    errors = errors_by_row(report)
    assert "does not belong to RCU" in errors[2]
    assert "already assigned to a different DCU" in errors[3]


@pytest.mark.asyncio
async def test_validate_invalid_active_and_code_values(pune):
    report = await master_import.validate(
        pune,
        parse(
            "RCU Pune,DCU Nashik,SBU 4,90000001,Bad Active,Maybe",
            "RCU Pune,DCU Nashik,SBU 4,5.72E+07,Scientific,Yes",
            "RCU Pune,DCU Nashik,SBU 4,57 21,Space In Code,Yes",
            "RCU Pune,DCU Nashik,SBU 4,057210164,Leading Zero Twin,Yes",
            "RCU Pune,DCU Nashik,SBU 4,90000005,Good,No",
        ),
    )
    errors = errors_by_row(report)
    assert "Invalid Active value 'Maybe'" in errors[2]
    assert "scientific notation" in errors[3]
    assert "Invalid ALC Code" in errors[4]
    assert f"differs only in leading zeros from existing '{JAYESH}'" in errors[5]
    assert 6 not in errors


@pytest.mark.asyncio
async def test_validate_writes_nothing(pune):
    before = await snapshot(pune)
    sbu = await pune.scalar(select(SBU).where(SBU.code == "SBU PN1"))
    report = await master_import.validate(
        pune,
        parse(
            "RCU Pune,DCU Pune North,SBU PN1,90000001,New,Yes",
            f"RCU Pune,DCU Nashik,SBU 4,{JAYESH},Renamed,Yes",
        ),
    )
    assert report["counts"]["create"] == 1 and report["counts"]["update"] == 1
    await pune.rollback()
    assert await snapshot(pune) == before
    await pune.refresh(sbu)
    assert sbu.dcu_id is None


def test_offline_validation_needs_no_database():
    records = master_import.parse_source(
        "m.csv",
        csv_bytes(
            HEADER,
            "RCU Pune,DCU Nashik,SBU 4,90000001,One,Yes",
            "RCU Pune,DCU Pune South,SBU 4,90000002,Two,Maybe",
        ),
    )
    report = validate_alc_master.offline_report(records)
    assert report["valid"] is False
    assert {row["row"] for row in report["invalid"]} == {2, 3}


# --------------------------------------------------------------------------- #
# Nashik mapping
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_nashik_master_counts_are_preserved(pune):
    assert await sbu_counts(pune) == NASHIK
    before = await snapshot(pune)
    records = master_import.parse_source("nashik.csv", nashik_canonical())
    summary = await master_import.perform(pune, records)

    assert (summary["created"], summary["updated"], summary["unchanged"]) == (0, 0, 199)
    assert summary["nashik"]["changed"] is False
    assert summary["nashik"]["after_total"] == 199
    assert {k: v["total"] for k, v in summary["nashik"]["after"].items()} == NASHIK
    assert await sbu_counts(pune) == NASHIK
    assert sum(NASHIK.values()) == 199 == await count(pune, ALC)
    assert await snapshot(pune) == before  # same ids, same rows, untouched

    nashik = await pune.scalar(select(DCU).where(DCU.code == "DCU_NASHIK"))
    linked = (await pune.scalars(select(SBU.code).where(SBU.dcu_id == nashik.id))).all()
    assert sorted(linked) == ["SBU 4", "SBU 6", "SBU 7"]


@pytest.mark.asyncio
async def test_import_blocked_if_it_would_change_nashik_counts(pune):
    before = await snapshot(pune)
    records = parse(f"RCU Pune,DCU Pune North,SBU PN1,{JAYESH},Jayesh Computers,Yes")
    with pytest.raises(master_import.MasterImportBlocked) as exc:
        await master_import.perform(pune, records)
    assert exc.value.report["nashik"]["changed"] is True
    assert exc.value.report["nashik"]["after"]["SBU 4"]["total"] == 70
    assert await snapshot(pune) == before


@pytest.mark.asyncio
async def test_fresh_database_loads_nashik_master(hierarchy_only):
    session = hierarchy_only
    records = master_import.parse_source("nashik.csv", nashik_canonical())
    with pytest.raises(master_import.MasterImportBlocked):
        await master_import.perform(session, records)  # counts go 0 -> 199: must be explicit
    assert await count(session, ALC) == 0

    summary = await master_import.perform(session, records, allow_nashik_change=True)
    assert summary["created"] == 199
    assert await sbu_counts(session) == NASHIK


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_rerunning_same_file_changes_nothing(pune):
    content = csv_bytes(
        HEADER,
        "RCU Pune,DCU Pune North,SBU PN1,90000001,New Pune North Centre,Yes",
        "RCU Pune,DCU Pune North,SBU PN1,90000002,Dormant Centre,No",
        f"RCU Pune,DCU Nashik,SBU 4,{JAYESH},Jayesh Computers Renamed,Yes",
    )
    first = await master_import.perform(pune, master_import.parse_source("m.csv", content))
    assert (first["created"], first["updated"], first["unchanged"]) == (2, 1, 0)
    assert first["sbus_linked"] == [{"sbu": "SBU PN1", "dcu": "DCU_PUNE_NORTH"}]

    after_first = await snapshot(pune)
    audits = await count(pune, AuditLog)
    second = await master_import.perform(pune, master_import.parse_source("m.csv", content))
    assert (second["created"], second["updated"], second["unchanged"]) == (0, 0, 3)
    assert second["sbus_linked"] == []
    assert await snapshot(pune) == after_first  # no new rows, no field or timestamp change
    assert await count(pune, AuditLog) == audits  # a no-op run leaves no audit entry
    assert await count(pune, ALC) == 201
    dormant = await pune.scalar(select(ALC).where(ALC.alc_code == "90000002"))
    assert dormant.status == AlcStatus.INACTIVE


@pytest.mark.asyncio
async def test_import_records_one_audit_entry_for_changes(pune):
    await master_import.perform(
        pune,
        parse("RCU Pune,DCU Pune North,SBU PN1,90000001,New,Yes"),
        source="m.csv",
    )
    entry = await pune.scalar(select(AuditLog).where(AuditLog.action == "ALC_MASTER_IMPORT"))
    assert entry.audit_metadata["created"] == 1
    assert entry.audit_metadata["source"] == "m.csv"


@pytest.mark.asyncio
async def test_absent_alcs_are_never_deleted(pune):
    summary = await master_import.perform(
        pune, parse(f"RCU Pune,DCU Nashik,SBU 4,{JAYESH},Jayesh Computers,Yes")
    )
    assert summary["unchanged"] == 1
    assert len(summary["not_in_source"]) == 198
    assert await count(pune, ALC) == 199
    assert await sbu_counts(pune) == NASHIK


@pytest.mark.asyncio
async def test_blocked_import_writes_nothing(pune):
    before = await snapshot(pune)
    records = parse(
        "RCU Pune,DCU Pune North,SBU PN1,90000001,Valid New,Yes",
        f"RCU Pune,DCU Nashik,SBU 4,{JAYESH},Valid Rename,Yes",
        "RCU Pune,DCU Nashik,SBU 99,90000002,Unknown SBU,Yes",
    )
    with pytest.raises(master_import.MasterImportBlocked) as exc:
        await master_import.perform(pune, records)
    assert exc.value.report["counts"]["invalid"] == 1
    assert await snapshot(pune) == before
    pn1 = await pune.scalar(select(SBU).where(SBU.code == "SBU PN1"))
    assert pn1.dcu_id is None


# --------------------------------------------------------------------------- #
# Safe update / reassignment with full history preserved
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_reassignment_preserves_id_users_and_history(pune):
    alc = await pune.scalar(select(ALC).where(ALC.alc_code == JAYESH))
    alc_id = alc.id
    sbu4 = await pune.scalar(select(SBU).where(SBU.code == "SBU 4"))
    alc_user = User(
        username="jayesh",
        password_hash=hash_password("StrongAlcPass123!"),
        role=Role.ALC,
        alc_id=alc_id,
        must_change_password=False,
    )
    sbu_user = User(
        username="sbu4-lead",
        password_hash=hash_password("StrongSbuPass123!"),
        role=Role.SBU,
        sbu_id=sbu4.id,
    )
    pune.add_all([alc_user, sbu_user])
    await pune.flush()
    partner = Partner(
        alc_id=alc_id, partner_name="Alpha School", partner_type="School", ecosystem="School"
    )
    pune.add(partner)
    await pune.flush()
    activity = Activity(
        activity_number="ACT-PRESERVE-1",
        alc_id=alc_id,
        partner_id=partner.id,
        activity_type="Partner meeting",
        ecosystem="School",
        activity_date=date.today(),
        location="Nashik",
        description="Meeting",
        outcome="Agreed",
        status=ActivityStatus.VERIFIED,
        created_by=alc_user.id,
    )
    pune.add(activity)
    await pune.flush()
    pune.add_all(
        [
            ActivityEvidence(
                activity_id=activity.id,
                storage_key="evidence/preserve-1.jpg",
                original_filename="photo.jpg",
                mime_type="image/jpeg",
                file_size=1024,
                uploaded_by=alc_user.id,
            ),
            ActivityReview(
                activity_id=activity.id,
                reviewer_id=sbu_user.id,
                previous_status=ActivityStatus.SUBMITTED,
                new_status=ActivityStatus.VERIFIED,
                action=ReviewAction.VERIFY,
                reviewer_role="SBU",
            ),
            ActivityRevision(
                activity_id=activity.id,
                revision_number=1,
                changed_by=alc_user.id,
                change_summary="Created",
                snapshot={"outcome": "Agreed"},
            ),
        ]
    )
    await pune.commit()
    partner_id, activity_id = partner.id, activity.id
    hashes = dict((await pune.execute(select(User.username, User.password_hash))).all())
    users_before = await count(pune, User)

    # Move the ALC out of Nashik into SBU PN1 (placed under DCU Pune North by this import),
    # rename it and mark it inactive.
    summary = await master_import.perform(
        pune,
        parse(f"RCU Pune,DCU Pune North,SBU PN1,{JAYESH},Jayesh Computers Pune,No"),
        allow_nashik_change=True,
    )
    assert (summary["created"], summary["updated"]) == (0, 1)

    pune.expire_all()
    moved = await pune.scalar(select(ALC).where(ALC.alc_code == JAYESH))
    pn1 = await pune.scalar(select(SBU).where(SBU.code == "SBU PN1"))
    north = await pune.scalar(select(DCU).where(DCU.code == "DCU_PUNE_NORTH"))
    assert moved.id == alc_id  # same database id: nothing recreated
    assert moved.sbu_id == pn1.id and pn1.dcu_id == north.id
    assert moved.alc_name == "Jayesh Computers Pune" and moved.status == AlcStatus.INACTIVE
    assert await count(pune, ALC) == 199

    # Logins and passwords untouched; history still attached to the same ALC id.
    assert await count(pune, User) == users_before
    assert dict((await pune.execute(select(User.username, User.password_hash))).all()) == hashes
    refreshed_user = await pune.scalar(select(User).where(User.username == "jayesh"))
    assert refreshed_user.alc_id == alc_id and refreshed_user.must_change_password is False
    assert await pune.scalar(select(Partner.alc_id).where(Partner.id == partner_id)) == alc_id
    kept = await pune.scalar(select(Activity).where(Activity.id == activity_id))
    assert kept.alc_id == alc_id and kept.status == ActivityStatus.VERIFIED
    assert await count(pune, ActivityEvidence) == 1
    assert await count(pune, ActivityReview) == 1
    assert await count(pune, ActivityRevision) == 1

    assert await sbu_counts(pune) == {"SBU 4": 70, "SBU 6": 58, "SBU 7": 70, "SBU PN1": 1}


@pytest.mark.asyncio
async def test_import_never_creates_hierarchy_records(pune):
    counts = (await count(pune, RCU), await count(pune, DCU), await count(pune, SBU))
    await master_import.perform(pune, parse("RCU Pune,DCU Pune North,SBU PN1,90000001,New,Yes"))
    assert (await count(pune, RCU), await count(pune, DCU), await count(pune, SBU)) == counts
