"""Phase 3B SBU seed for DCU Ahilya Nagar, Pune North and Pune South.

The ``prod`` fixture reproduces the current production state: RCU Pune with its four DCUs
(``ensure_hierarchy``), SBU 4 / 6 / 7 under DCU Nashik holding the 199-ALC Nashik master,
plus one ALC login, an SBU login, and a partner / activity / evidence / review / revision on
a Nashik ALC so that any unintended mutation is detected."""

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.auth import hash_password
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
    Partner,
    User,
)
from app.services import alc_import, master_import, sbu_master
from app.services.hierarchy import DCUS, ensure_hierarchy
from app.services.master_mappings import (
    DCU_CODES,
    EXPECTED_ALC_COUNTS,
    NEW_DCUS,
    SBU_ALIASES,
    SBU_DISPLAY_NAMES,
    SBUS_BY_DCU,
    remaining_sbus,
)

DATA = Path(__file__).resolve().parents[3] / "data"
NASHIK_MASTER = DATA / "ALC-MASTER.csv"
NEW_MASTER = DATA / "RCU-PUNE-NEW-ALCS.csv"
NASHIK = {
    "SBU 4": {"dcu": "DCU_NASHIK", "alcs": 71, "active": 71},
    "SBU 6": {"dcu": "DCU_NASHIK", "alcs": 58, "active": 58},
    "SBU 7": {"dcu": "DCU_NASHIK", "alcs": 70, "active": 70},
}
EXPECTED_DCU = {
    "Ahilyanagar_sbu1": "DCU_AHILYA_NAGAR",
    "Ahilyanagar_sbu2": "DCU_AHILYA_NAGAR",
    "Ahilyanagar_sbu5": "DCU_AHILYA_NAGAR",
    "Ahilyanagar_sbu10": "DCU_AHILYA_NAGAR",
    "SBU_Pune_North_1": "DCU_PUNE_NORTH",
    "SBU_Pune_North_2": "DCU_PUNE_NORTH",
    "SBU_Pune_North_3": "DCU_PUNE_NORTH",
    "SBU_Pune_North_4": "DCU_PUNE_NORTH",
    "SBU_Pune_North_5": "DCU_PUNE_NORTH",
    "pune_south_sbu_2": "DCU_PUNE_SOUTH",
    "pune_south_sbu_3": "DCU_PUNE_SOUTH",
    "pune_south_sbu_4": "DCU_PUNE_SOUTH",
}


async def count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


async def dcu_id(session, code):
    return await session.scalar(select(DCU.id).where(DCU.code == code))


async def sbu_placement(session) -> dict[str, str | None]:
    rows = await session.execute(
        select(SBU.code, DCU.code).select_from(SBU).outerjoin(DCU, SBU.dcu_id == DCU.id)
    )
    return dict(rows.all())


async def everything_but_sbus(session) -> dict[str, list[tuple]]:
    """Full row snapshot of every table the seed must never touch."""

    async def rows(model, *columns):
        result = await session.execute(select(*columns).order_by(model.id))
        return [tuple(r) for r in result.all()]

    return {
        "alcs": await rows(
            ALC, ALC.id, ALC.alc_code, ALC.alc_name, ALC.sbu_id, ALC.status, ALC.updated_at
        ),
        "users": await rows(
            User,
            User.id,
            User.username,
            User.password_hash,
            User.role,
            User.alc_id,
            User.sbu_id,
            User.dcu_id,
            User.is_active,
            User.must_change_password,
            User.updated_at,
        ),
        "activities": await rows(
            Activity,
            Activity.id,
            Activity.alc_id,
            Activity.status,
            Activity.partner_id,
            Activity.updated_at,
        ),
        "partners": await rows(
            Partner, Partner.id, Partner.alc_id, Partner.partner_name, Partner.updated_at
        ),
        "evidence": await rows(
            ActivityEvidence,
            ActivityEvidence.id,
            ActivityEvidence.activity_id,
            ActivityEvidence.storage_key,
            ActivityEvidence.is_active,
        ),
        "reviews": await rows(
            ActivityReview,
            ActivityReview.id,
            ActivityReview.activity_id,
            ActivityReview.new_status,
            ActivityReview.remark,
        ),
        "revisions": await rows(
            ActivityRevision,
            ActivityRevision.id,
            ActivityRevision.activity_id,
            ActivityRevision.snapshot,
        ),
        "rcus": await rows(RCU, RCU.id, RCU.code, RCU.name, RCU.updated_at),
        "dcus": await rows(DCU, DCU.id, DCU.code, DCU.rcu_id, DCU.updated_at),
    }


@pytest.fixture
async def prod(session):
    session.add_all([SBU(code=f"SBU {n}", name=f"Strategic Business Unit {n}") for n in (4, 6, 7)])
    await session.flush()
    await ensure_hierarchy(session)
    await session.commit()
    records = alc_import.parse_source(NASHIK_MASTER.name, NASHIK_MASTER.read_bytes())
    await alc_import.perform(session, records)

    alc = await session.scalar(select(ALC).where(ALC.alc_code == "57210164"))
    sbu4 = await session.scalar(select(SBU).where(SBU.code == "SBU 4"))
    alc_user = User(
        username="jayesh",
        password_hash=hash_password("StrongAlcPass123!"),
        role=Role.ALC,
        alc_id=alc.id,
    )
    sbu_user = User(
        username="sbu4",
        password_hash=hash_password("StrongSbuPass123!"),
        role=Role.SBU,
        sbu_id=sbu4.id,
    )
    session.add_all([alc_user, sbu_user])
    await session.flush()
    partner = Partner(
        alc_id=alc.id, partner_name="Alpha School", partner_type="School", ecosystem="School"
    )
    session.add(partner)
    await session.flush()
    activity = Activity(
        activity_number="ACT-3B-1",
        alc_id=alc.id,
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
    session.add(activity)
    await session.flush()
    session.add_all(
        [
            ActivityEvidence(
                activity_id=activity.id,
                storage_key="evidence/3b-1.jpg",
                original_filename="p.jpg",
                mime_type="image/jpeg",
                file_size=10,
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
    await session.commit()
    return session


# --------------------------------------------------------------------------- #
# Mapping table
# --------------------------------------------------------------------------- #
def test_mapping_table_is_consistent():
    assert set(DCU_CODES.values()) == {code for code, _ in DCUS}
    assert dict(remaining_sbus()) == EXPECTED_DCU
    for dcu, sbus in SBUS_BY_DCU.items():
        assert set(EXPECTED_ALC_COUNTS[dcu]) == set(sbus)
    for dcu, aliases in SBU_ALIASES.items():
        assert set(aliases.values()) <= set(SBUS_BY_DCU[dcu])
    totals = {d: sum(c.values()) for d, c in EXPECTED_ALC_COUNTS.items()}
    assert totals == {"Ahilya Nagar": 203, "Pune North": 263, "Pune South": 119, "Nashik": 199}
    assert sum(totals[d] for d in NEW_DCUS) == 585 and sum(totals.values()) == 784


FRIENDLY = {
    "Ahilyanagar_sbu1": "Ahilya Nagar SBU 1",
    "Ahilyanagar_sbu2": "Ahilya Nagar SBU 2",
    "Ahilyanagar_sbu5": "Ahilya Nagar SBU 5",
    "Ahilyanagar_sbu10": "Ahilya Nagar SBU 10",
    "SBU_Pune_North_1": "Pune North SBU 1",
    "SBU_Pune_North_2": "Pune North SBU 2",
    "SBU_Pune_North_3": "Pune North SBU 3",
    "SBU_Pune_North_4": "Pune North SBU 4",
    "SBU_Pune_North_5": "Pune North SBU 5",
    "pune_south_sbu_2": "Pune South SBU 2 - Ajinkya Chavan",
    "pune_south_sbu_3": "Pune South SBU 3 - Aniket Marne",
    "pune_south_sbu_4": "Pune South SBU 4 - Bhagyashree Gaikwad",
}


def test_display_names_are_the_confirmed_ones():
    assert SBU_DISPLAY_NAMES == FRIENDLY
    assert set(SBU_DISPLAY_NAMES) == set(EXPECTED_DCU)


def test_canonical_identifiers_never_collide_under_importer_spelling_rules():
    # Codes and display names together: no two SBUs may share a spelling key, or the
    # Phase 3A importer could not resolve an SBU unambiguously.
    all_sbus = [s for sbus in SBUS_BY_DCU.values() for s in sbus]
    keys = [sbu_master._keys(s) | sbu_master._keys(SBU_DISPLAY_NAMES.get(s, s)) for s in all_sbus]
    for i, a in enumerate(keys):
        for j, b in enumerate(keys):
            if i != j:
                assert not (a & b), (all_sbus[i], all_sbus[j])


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_seed_creates_missing_sbus_under_correct_dcu(prod):
    report = await sbu_master.seed_remaining_sbus(prod)
    await prod.commit()
    assert report["counts"] == {"create": 12, "link": 0, "unchanged": 0}
    placement = await sbu_placement(prod)
    for code, dcu in EXPECTED_DCU.items():
        assert placement[code] == dcu
    assert await count(prod, SBU) == 15
    seeded = (await prod.scalars(select(SBU).where(SBU.code.in_(EXPECTED_DCU)))).all()
    assert {s.code: s.name for s in seeded} == FRIENDLY  # code canonical, name friendly
    assert all(s.is_active for s in seeded)
    audit = await prod.scalar(select(AuditLog).where(AuditLog.action == "SBU_MASTER_SEED"))
    assert len(audit.audit_metadata["changes"]) == 12


@pytest.mark.asyncio
async def test_seed_is_idempotent(prod):
    await sbu_master.seed_remaining_sbus(prod)
    await prod.commit()
    placement = await sbu_placement(prod)
    audits = await count(prod, AuditLog)
    second = await sbu_master.seed_remaining_sbus(prod)
    await prod.commit()
    assert second["counts"] == {"create": 0, "link": 0, "unchanged": 12}
    assert await sbu_placement(prod) == placement
    assert await count(prod, SBU) == 15
    assert await count(prod, AuditLog) == audits


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(prod):
    report = await sbu_master.seed_remaining_sbus(prod, dry_run=True)
    await prod.rollback()
    assert report["counts"]["create"] == 12
    assert await count(prod, SBU) == 3


@pytest.mark.asyncio
async def test_existing_correct_sbu_left_unchanged_not_duplicated(prod):
    existing = SBU(
        code="SBU_Pune_North_3",
        name="North Three (admin name)",
        dcu_id=await dcu_id(prod, "DCU_PUNE_NORTH"),
    )
    prod.add(existing)
    await prod.commit()
    await prod.refresh(existing)  # compare database values, not the in-memory originals
    before = (existing.id, existing.name, existing.updated_at)
    report = await sbu_master.seed_remaining_sbus(prod)
    await prod.commit()
    assert report["counts"] == {"create": 11, "link": 0, "unchanged": 1}
    item = next(i for i in report["items"] if i["sbu"] == "SBU_Pune_North_3")
    assert "not renamed" in item["note"]
    assert await prod.scalar(select(func.count(SBU.id)).where(SBU.code == "SBU_Pune_North_3")) == 1
    await prod.refresh(existing)
    assert (existing.id, existing.name, existing.updated_at) == before


@pytest.mark.asyncio
async def test_existing_unplaced_sbu_is_linked_not_duplicated(prod):
    existing = SBU(code="pune_south_sbu_2", name="pune_south_sbu_2")
    prod.add(existing)
    await prod.commit()
    report = await sbu_master.seed_remaining_sbus(prod)
    await prod.commit()
    assert report["counts"] == {"create": 11, "link": 1, "unchanged": 0}
    await prod.refresh(existing)
    assert existing.dcu_id == await dcu_id(prod, "DCU_PUNE_SOUTH")
    assert await count(prod, SBU) == 15


@pytest.mark.asyncio
async def test_existing_sbu_under_other_dcu_blocks_and_is_not_moved(prod):
    south = await dcu_id(prod, "DCU_PUNE_SOUTH")
    prod.add(SBU(code="SBU_Pune_North_1", name="SBU_Pune_North_1", dcu_id=south))
    await prod.commit()
    with pytest.raises(sbu_master.SbuSeedBlocked) as exc:
        await sbu_master.seed_remaining_sbus(prod)
    await prod.rollback()
    item = next(i for i in exc.value.report["items"] if i["sbu"] == "SBU_Pune_North_1")
    assert item["action"] == "conflict" and "DCU_PUNE_SOUTH" in item["reason"]
    assert await count(prod, SBU) == 4  # nothing else created either
    assert (await sbu_placement(prod))["SBU_Pune_North_1"] == "DCU_PUNE_SOUTH"


@pytest.mark.asyncio
async def test_existing_spelling_variant_blocks(prod):
    prod.add(SBU(code="SBU Pune North 1", name="Pune North SBU One"))
    await prod.commit()
    with pytest.raises(sbu_master.SbuSeedBlocked) as exc:
        await sbu_master.seed_remaining_sbus(prod)
    await prod.rollback()
    item = next(i for i in exc.value.report["items"] if i["sbu"] == "SBU_Pune_North_1")
    assert item["action"] == "variant" and item["existing"] == "SBU Pune North 1"
    assert await count(prod, SBU) == 4


@pytest.mark.asyncio
async def test_display_name_used_by_another_code_blocks(prod):
    prod.add(SBU(code="PS-SOUTH-4", name="Pune South SBU 4 - Bhagyashree Gaikwad"))
    await prod.commit()
    with pytest.raises(sbu_master.SbuSeedBlocked) as exc:
        await sbu_master.seed_remaining_sbus(prod)
    await prod.rollback()
    item = next(i for i in exc.value.report["items"] if i["sbu"] == "pune_south_sbu_4")
    assert item["action"] == "variant" and item["existing"] == "PS-SOUTH-4"
    assert await count(prod, SBU) == 4


@pytest.mark.asyncio
async def test_ambiguous_existing_sbus_block(prod):
    prod.add_all(
        [
            SBU(code="sbu_pune_north_2", name="sbu_pune_north_2"),
            SBU(code="PN-TWO", name="SBU Pune North 2"),
        ]
    )
    await prod.commit()
    with pytest.raises(sbu_master.SbuSeedBlocked) as exc:
        await sbu_master.seed_remaining_sbus(prod)
    await prod.rollback()
    item = next(i for i in exc.value.report["items"] if i["sbu"] == "SBU_Pune_North_2")
    assert item["action"] == "ambiguous"
    assert "PN-TWO" in item["reason"] and "sbu_pune_north_2" in item["reason"]
    assert await count(prod, SBU) == 5


@pytest.mark.asyncio
async def test_missing_dcu_master_blocks(session):
    session.add(SBU(code="SBU 4", name="SBU 4"))
    await session.commit()
    with pytest.raises(sbu_master.SbuSeedBlocked) as exc:
        await sbu_master.seed_remaining_sbus(session)
    await session.rollback()
    assert any("seed_hierarchy" in b for b in exc.value.report["blockers"])
    assert await count(session, SBU) == 1 and await count(session, DCU) == 0


# --------------------------------------------------------------------------- #
# Nashik protection and no collateral mutation
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_nashik_hierarchy_and_counts_unchanged(prod):
    assert await sbu_master.nashik_snapshot(prod) == NASHIK
    report = await sbu_master.seed_remaining_sbus(prod)
    await prod.commit()
    assert report["nashik_before"] == report["nashik_after"] == NASHIK
    assert await sbu_master.nashik_snapshot(prod) == NASHIK
    assert sum(v["alcs"] for v in NASHIK.values()) == 199 == await count(prod, ALC)


@pytest.mark.asyncio
async def test_seed_touches_no_alc_user_activity_partner_evidence_review(prod):
    before = await everything_but_sbus(prod)
    assert all(before[k] for k in before)  # the fixture really has data in each table
    await sbu_master.seed_remaining_sbus(prod)
    await prod.commit()
    await sbu_master.seed_remaining_sbus(prod)  # and again
    await prod.commit()
    prod.expire_all()
    assert await everything_but_sbus(prod) == before


# --------------------------------------------------------------------------- #
# Seed + canonical master through the Phase 3A importer (throwaway test DB only)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_new_master_unknown_sbus_until_seeded(prod):
    records = master_import.parse_source(NEW_MASTER.name, NEW_MASTER.read_bytes())
    report = await master_import.validate(prod, records)
    assert report["counts"]["invalid"] == 585
    assert all(e.startswith("Unknown SBU") for row in report["invalid"] for e in row["errors"])


@pytest.mark.asyncio
async def test_seeded_hierarchy_accepts_new_master_as_585_creates(prod):
    await sbu_master.seed_remaining_sbus(prod)
    await prod.commit()
    records = master_import.parse_source(NEW_MASTER.name, NEW_MASTER.read_bytes())
    report = await master_import.validate(prod, records)
    assert report["valid"], report["invalid"][:3]
    assert report["counts"] == {
        "total": 585,
        "create": 585,
        "update": 0,
        "unchanged": 0,
        "invalid": 0,
    }
    assert report["sbu_links"] == []  # every SBU already placed by the seed
    assert {r["sbu"] for r in report["rows"]} == set(EXPECTED_DCU)  # resolved by code
    assert report["nashik"]["changed"] is False
    await prod.rollback()
    assert await count(prod, ALC) == 199  # validation is a dry run


@pytest.mark.asyncio
async def test_end_to_end_in_test_db_reaches_784(prod):
    await sbu_master.seed_remaining_sbus(prod)
    await prod.commit()
    records = master_import.parse_source(NEW_MASTER.name, NEW_MASTER.read_bytes())
    summary = await master_import.perform(prod, records)
    assert (summary["created"], summary["updated"], summary["unchanged"]) == (585, 0, 0)
    per_dcu = {}
    for row in await sbu_master.hierarchy_tree(prod):
        per_dcu[row["dcu"]] = per_dcu.get(row["dcu"], 0) + row["alcs"]
    assert per_dcu == {
        "DCU_AHILYA_NAGAR": 203,
        "DCU_NASHIK": 199,
        "DCU_PUNE_NORTH": 263,
        "DCU_PUNE_SOUTH": 119,
    }
    assert await count(prod, ALC) == 784
    assert await sbu_master.nashik_snapshot(prod) == NASHIK
    again = await master_import.perform(prod, records)
    assert (again["created"], again["updated"], again["unchanged"]) == (0, 0, 585)
