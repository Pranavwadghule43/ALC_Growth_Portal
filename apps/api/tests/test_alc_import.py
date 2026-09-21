"""Real ALC master import: parsing, validation classification, SBU mapping, transactional
upsert by ALC Code, preservation of existing records, and the admin import endpoints.

Uses the seeded SBUs (``SBU 4``, ``SBU 6``) and ALCs (Centre A/00010001 -> SBU 4,
Centre B/00010002 -> SBU 6, Centre C/00010003 -> SBU 4)."""
import pytest
from sqlalchemy import func, select

from app.enums import Role
from app.models import ALC, SBU, Activity, Partner, User
from app.services import alc_import
from tests.conftest import login


def csv_bytes(*rows: str) -> bytes:
    return ("\n".join(rows) + "\n").encode("utf-8")


HEADER = "ALC Code,ALC Name,SBU"
HEADER_WITH_DCU = "ALC Code,ALC Name,DCU,SBU"  # mirrors the real source; DCU must be ignored


# --- Parsing --------------------------------------------------------------- #
def test_parse_ignores_dcu_and_empty_rows():
    content = csv_bytes(
        HEADER_WITH_DCU,
        "57210164,Jayesh Computers,Nashik,SBU 4",
        ",,,",
        "57210168,Shree Computers,Nashik,SBU 7",
        "",
    )
    records = alc_import.parse_source("master.csv", content)
    assert [(r.alc_code, r.alc_name, r.sbu) for r in records] == [
        ("57210164", "Jayesh Computers", "SBU 4"),
        ("57210168", "Shree Computers", "SBU 7"),
    ]


def test_parse_preserves_leading_zeros_and_trims():
    content = csv_bytes(HEADER, "  00012345 ,  Zero Centre  ,  SBU 4 ")
    (record,) = alc_import.parse_source("master.csv", content)
    assert record.alc_code == "00012345" and record.alc_name == "Zero Centre"
    assert record.sbu == "SBU 4"


def test_parse_requires_columns():
    with pytest.raises(ValueError):
        alc_import.parse_source("master.csv", csv_bytes("ALC Code,ALC Name", "1,Two"))


# --- Validation (no writes) ------------------------------------------------ #
@pytest.mark.asyncio
async def test_validate_classifies(session, seeded):
    content = csv_bytes(
        HEADER,
        "00010001,Centre A,SBU 4",  # unchanged
        "00010002,Centre B Renamed,SBU 6",  # update (name)
        "00010003,Centre C,SBU 6",  # update (sbu 4 -> 6)
        "99990001,Brand New Centre,SBU 4",  # new
    )
    records = alc_import.parse_source("m.csv", content)
    report = await alc_import.validate(session, records)
    assert report["counts"] == {"total": 4, "new": 1, "update": 2, "unchanged": 1, "invalid": 0}
    # Validation must not write anything.
    assert await session.scalar(select(func.count(ALC.id))) == 3


@pytest.mark.asyncio
async def test_validate_flags_unknown_sbu_blank_and_duplicate(session, seeded):
    content = csv_bytes(
        HEADER,
        "88880001,Ghost Centre,SBU 9",  # unknown SBU
        "88880002,,SBU 4",  # blank name
        ",No Code,SBU 4",  # blank code
        "77770001,First,SBU 4",
        "77770001,Dup,SBU 6",  # duplicate code
    )
    records = alc_import.parse_source("m.csv", content)
    report = await alc_import.validate(session, records)
    assert report["counts"]["invalid"] == 4
    assert report["missing_sbus"] == ["SBU 9"]
    assert report["duplicates"] == ["77770001"]


# --- Import (transactional upsert) ----------------------------------------- #
@pytest.mark.asyncio
async def test_perform_upserts_and_preserves(session, seeded):
    users_before = await session.scalar(select(func.count(User.id)))
    alc_a_id = seeded["alc_a"].id
    content = csv_bytes(
        HEADER,
        "00010001,Centre A,SBU 4",  # unchanged
        "00010002,Centre B Renamed,SBU 6",  # update name
        "00010003,Centre C,SBU 6",  # update sbu
        "99990001,Brand New Centre,SBU 4",  # new
    )
    records = alc_import.parse_source("m.csv", content)
    summary = await alc_import.perform(session, records)
    assert summary["created"] == 1
    assert summary["updated"] == 2
    assert summary["unchanged"] == 1
    assert summary["skipped"] == 0 and summary["failed"] == 0

    # New ALC exists; existing IDs preserved; name/sbu updated in place.
    new = await session.scalar(select(ALC).where(ALC.alc_code == "99990001"))
    assert new is not None and new.sbu_id == seeded["sbu4"].id
    await session.refresh(seeded["alc_a"])
    await session.refresh(seeded["alc_b"])
    await session.refresh(seeded["alc_c"])
    assert seeded["alc_a"].id == alc_a_id  # same DB id, not recreated
    assert seeded["alc_b"].alc_name == "Centre B Renamed"
    assert seeded["alc_c"].sbu_id == seeded["sbu6"].id
    # No user accounts created or removed by the import.
    assert await session.scalar(select(func.count(User.id))) == users_before


@pytest.mark.asyncio
async def test_perform_blocked_on_unknown_sbu_writes_nothing(session, seeded):
    before = await session.scalar(select(func.count(ALC.id)))
    content = csv_bytes(HEADER, "99990002,Valid New,SBU 4", "99990003,Bad SBU,SBU 99")
    records = alc_import.parse_source("m.csv", content)
    with pytest.raises(alc_import.ImportBlocked) as exc:
        await alc_import.perform(session, records)
    assert exc.value.detail["missing_sbus"] == ["SBU 99"]
    # Nothing written, including the otherwise-valid row.
    assert await session.scalar(select(func.count(ALC.id))) == before


@pytest.mark.asyncio
async def test_orphan_codes_reported_not_deleted(session, seeded):
    content = csv_bytes(HEADER, "00010001,Centre A,SBU 4")  # omits B and C
    records = alc_import.parse_source("m.csv", content)
    orphans = await alc_import.orphan_codes(session, records)
    assert orphans == ["00010002", "00010003"]
    # Reporting only; the ALCs still exist.
    assert await session.scalar(select(func.count(ALC.id))) == 3


# --- Hard reset (replace) -------------------------------------------------- #
@pytest.mark.asyncio
async def test_hard_reset_wipes_alc_domain_keeps_admin_and_sbu(session, seeded):
    from datetime import date

    session.add(
        Partner(alc_id=seeded["alc_a"].id, partner_name="Alpha", partner_type="School",
                ecosystem="School")
    )
    session.add(
        Activity(
            alc_id=seeded["alc_a"].id,
            activity_number="ACT-1",
            activity_type="Partner meeting",
            ecosystem="College",
            activity_date=date.today(),
            location="Pune",
            description="A meeting",
            outcome="Agreed",
            created_by=seeded["a"].id,
        )
    )
    await session.commit()

    removed = await alc_import.hard_reset_alc_domain(session)
    await session.commit()
    assert removed["alcs"] == 3 and removed["alc_users"] == 3
    assert removed["partners"] == 1 and removed["activities"] == 1

    # ALC domain is empty...
    assert await session.scalar(select(func.count(ALC.id))) == 0
    assert await session.scalar(select(func.count(Partner.id))) == 0
    assert await session.scalar(select(func.count(Activity.id))) == 0
    assert await session.scalar(
        select(func.count(User.id)).where(User.role == Role.ALC)
    ) == 0
    # ...but ADMIN + SBU accounts and the SBU master remain.
    assert await session.scalar(select(func.count(User.id)).where(User.role == Role.ADMIN)) == 1
    assert await session.scalar(select(func.count(User.id)).where(User.role == Role.SBU)) == 2
    assert await session.scalar(select(func.count(SBU.id))) == 2


@pytest.mark.asyncio
async def test_hard_reset_then_import_lands_exactly_the_master(session, seeded):
    await alc_import.hard_reset_alc_domain(session)
    content = csv_bytes(HEADER, "70000001,New One,SBU 4", "70000002,New Two,SBU 6")
    records = alc_import.parse_source("m.csv", content)
    summary = await alc_import.perform(session, records)
    assert summary["created"] == 2
    codes = set((await session.scalars(select(ALC.alc_code))).all())
    assert codes == {"70000001", "70000002"}  # only the new master remains


# --- Admin endpoints ------------------------------------------------------- #
@pytest.mark.asyncio
async def test_import_endpoints_require_admin(client):
    await login(client, "sbu-4", "StrongSbuPass4!", "SBU")
    files = {"file": ("m.csv", csv_bytes(HEADER, "99990001,X,SBU 4"), "text/csv")}
    assert (await client.post("/api/admin/alcs/import/validate", files=files)).status_code == 403
    assert (await client.post("/api/admin/alcs/import", files=files)).status_code == 403


@pytest.mark.asyncio
async def test_admin_validate_then_import(client, session, seeded):
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    content = csv_bytes(
        HEADER,
        "00010002,Centre B Renamed,SBU 6",  # update
        "99990001,Brand New Centre,SBU 4",  # new
        "00010001,Centre A,SBU 4",  # unchanged
    )
    files = {"file": ("m.csv", content, "text/csv")}
    validated = await client.post("/api/admin/alcs/import/validate", files=files)
    assert validated.status_code == 200
    assert validated.json()["counts"] == {
        "total": 3, "new": 1, "update": 1, "unchanged": 1, "invalid": 0
    }

    imported = await client.post(
        "/api/admin/alcs/import", files={"file": ("m.csv", content, "text/csv")}
    )
    assert imported.status_code == 200
    body = imported.json()
    assert body["created"] == 1 and body["updated"] == 1 and body["unchanged"] == 1
    by_sbu = {row["sbu"]: row["alcs"] for row in body["by_sbu"]}
    assert by_sbu["SBU 4"] == 3 and by_sbu["SBU 6"] == 1  # C still SBU 4, +new; B under SBU 6

    listed = (await client.get("/api/admin/alcs?page=1&page_size=100")).json()
    codes = {row["alc_code"] for row in listed["items"]}
    assert "99990001" in codes


@pytest.mark.asyncio
async def test_admin_import_unknown_sbu_blocked(client, seeded):
    await login(client, "admin", "StrongAdminPass!", "ADMIN")
    content = csv_bytes(HEADER, "99990009,Ghost,SBU 42")
    resp = await client.post(
        "/api/admin/alcs/import", files={"file": ("m.csv", content, "text/csv")}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["missing_sbus"] == ["SBU 42"]
