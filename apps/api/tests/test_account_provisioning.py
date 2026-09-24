"""Phase 4A bulk provisioning of missing ALC and SBU login accounts.

The ``world`` fixture builds a small RCU Pune hierarchy (no real master data):

* SBU 4 / SBU 6 under DCU Nashik (via ``ensure_hierarchy``), SBU_Pune_North_1 under DCU Pune
  North, and ``SBU ORPHAN`` with no DCU;
* ALCs: two in SBU 4, one in SBU 6, one in Pune North, one INACTIVE ALC in SBU 6, one ALC
  without an SBU and one in the orphan SBU;
* an ADMIN and a DCU login (which provisioning must never create or touch).
"""

import argparse
import csv
import os
import stat
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.auth import hash_password, verify_password
from app.enums import AlcStatus, Role
from app.models import ALC, DCU, SBU, AuditLog, User
from app.services import account_provisioning as ap
from app.services.hierarchy import ensure_hierarchy
from app.services.scope import accessible_alc_ids, has_scope_key
from scripts import provision_accounts as cli
from tests.conftest import login

REPO = Path(__file__).resolve().parents[3]
KNOWN = "KnownPassword123!"


async def count(session, model, *where) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(*where))


async def users_snapshot(session) -> list[tuple]:
    rows = await session.execute(
        select(
            User.id,
            User.username,
            User.email,
            User.password_hash,
            User.role,
            User.alc_id,
            User.sbu_id,
            User.dcu_id,
            User.is_active,
            User.must_change_password,
        ).order_by(User.username)
    )
    return [tuple(r) for r in rows.all()]


def by_key(report) -> dict[str, dict]:
    return {(i["alc_code"] or i["sbu"]): i for i in report["items"]}


@pytest.fixture
async def world(session):
    sbu4 = SBU(code="SBU 4", name="Strategic Business Unit 4")
    sbu6 = SBU(code="SBU 6", name="Strategic Business Unit 6")
    session.add_all([sbu4, sbu6])
    await session.flush()
    await ensure_hierarchy(session)
    dcus = {d.code: d for d in (await session.scalars(select(DCU))).all()}
    north = SBU(code="SBU_Pune_North_1", name="Pune North SBU 1", dcu_id=dcus["DCU_PUNE_NORTH"].id)
    orphan = SBU(code="SBU ORPHAN", name="Orphan SBU")
    session.add_all([north, orphan])
    await session.flush()
    alcs = {
        "10000001": ALC(alc_code="10000001", alc_name="Nashik One", sbu_id=sbu4.id),
        "10000002": ALC(alc_code="10000002", alc_name="Nashik Two", sbu_id=sbu4.id),
        "10000003": ALC(alc_code="10000003", alc_name="Nashik Six", sbu_id=sbu6.id),
        "20000001": ALC(alc_code="20000001", alc_name="=North Centre", sbu_id=north.id),
        "30000001": ALC(
            alc_code="30000001", alc_name="Dormant", sbu_id=sbu6.id, status=AlcStatus.INACTIVE
        ),
        "40000001": ALC(alc_code="40000001", alc_name="No SBU", sbu_id=None),
        "50000001": ALC(alc_code="50000001", alc_name="Orphaned", sbu_id=orphan.id),
    }
    session.add_all(alcs.values())
    admin = User(username="admin", password_hash=hash_password(KNOWN), role=Role.ADMIN)
    dcu_user = User(
        username="dcu-nashik",
        password_hash=hash_password(KNOWN),
        role=Role.DCU,
        dcu_id=dcus["DCU_NASHIK"].id,
    )
    session.add_all([admin, dcu_user])
    await session.commit()
    return {
        "session": session,
        "alcs": alcs,
        "sbu4": sbu4,
        "sbu6": sbu6,
        "north": north,
        "orphan": orphan,
        "dcus": dcus,
    }


def factory_for(session):
    @asynccontextmanager
    async def factory():
        yield session

    return factory


def cli_args(**overrides):
    values = dict(
        role="alc",
        dry_run=False,
        execute=True,
        expect_create=None,
        out=None,
        allow_partial=True,
        show_creates=False,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


# --------------------------------------------------------------------------- #
# Planning / eligibility
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_alc_plan_classifies_every_master_record(world):
    report = await ap.plan(world["session"], "alc")
    items = by_key(report)
    assert {k: v["action"] for k, v in items.items()} == {
        "10000001": "create",
        "10000002": "create",
        "10000003": "create",
        "20000001": "create",
        "30000001": "inactive",
        "40000001": "invalid",
        "50000001": "invalid",
    }
    assert report["counts"] == {
        "total": 7,
        "eligible": 4,
        "create": 4,
        "existing": 0,
        "inactive": 1,
        "conflict": 0,
        "invalid": 2,
    }
    assert items["10000001"]["username"] == "alc-10000001"
    assert items["10000001"]["login_identifier"] == "10000001"
    assert items["20000001"]["dcu"] == "DCU Pune North" and items["20000001"]["rcu"] == "RCU Pune"


@pytest.mark.asyncio
async def test_sbu_plan_and_username_convention(world):
    report = await ap.plan(world["session"], "sbu")
    items = by_key(report)
    assert items["SBU 4"]["action"] == "create" and items["SBU 4"]["username"] == "sbu-4"
    assert items["SBU_Pune_North_1"]["username"] == "sbu-pune-north-1"
    assert items["SBU ORPHAN"]["action"] == "invalid"
    assert report["counts"]["create"] == 3


def test_username_helpers():
    assert ap.alc_username("00012345") == "alc-00012345"
    assert ap.sbu_username("SBU 4") == "sbu-4"
    assert ap.sbu_username("Ahilyanagar_sbu10") == "ahilyanagar-sbu10"
    assert ap.sbu_username("pune_south_sbu_2") == "pune-south-sbu-2"


# 9. missing SBU hierarchy / 10. missing DCU hierarchy
@pytest.mark.asyncio
async def test_missing_sbu_or_dcu_hierarchy_rejected(world):
    items = by_key(await ap.plan(world["session"], "alc"))
    assert items["40000001"]["action"] == "invalid"
    assert items["40000001"]["reason"] == "ALC has no SBU"
    assert items["50000001"]["action"] == "invalid"
    assert "has no DCU" in items["50000001"]["reason"]
    sbu_items = by_key(await ap.plan(world["session"], "sbu"))
    assert sbu_items["SBU ORPHAN"]["reason"] == "SBU 'SBU ORPHAN' has no DCU"


# 11. inactive ALC / 12. inactive SBU (and inactive DCU)
@pytest.mark.asyncio
async def test_inactive_alc_is_skipped(world):
    items = by_key(await ap.plan(world["session"], "alc"))
    assert items["30000001"]["action"] == "inactive"
    assert items["30000001"]["reason"] == "ALC is INACTIVE"


@pytest.mark.asyncio
async def test_inactive_sbu_and_dcu_are_skipped(world):
    session = world["session"]
    world["sbu6"].is_active = False
    world["dcus"]["DCU_PUNE_NORTH"].is_active = False
    await session.commit()
    sbu_items = by_key(await ap.plan(session, "sbu"))
    assert sbu_items["SBU 6"]["action"] == "inactive"
    assert sbu_items["SBU_Pune_North_1"]["action"] == "inactive"
    assert "DCU 'DCU_PUNE_NORTH' is inactive" in sbu_items["SBU_Pune_North_1"]["reason"]
    alc_items = by_key(await ap.plan(session, "alc"))
    assert alc_items["10000003"]["action"] == "inactive"  # ALC under the inactive SBU
    assert alc_items["20000001"]["action"] == "inactive"  # ALC under the inactive DCU
    _, credentials = await ap.provision(session, "sbu", allow_partial=True)
    await session.commit()
    assert {c.sbu for c in credentials} == {"SBU 4"}
    assert await count(session, User, User.sbu_id == world["sbu6"].id) == 0


# --------------------------------------------------------------------------- #
# Creation
# --------------------------------------------------------------------------- #
# 1, 13, 14, 18: missing ALC user created correctly, must change password, scope
@pytest.mark.asyncio
async def test_missing_alc_user_created_correctly(world):
    session = world["session"]
    report, credentials = await ap.provision(session, "alc", allow_partial=True)
    await session.commit()
    assert report["counts"]["create"] == 4 and len(credentials) == 4
    alc = world["alcs"]["10000001"]
    user = await session.scalar(select(User).where(User.alc_id == alc.id))
    assert user.username == "alc-10000001" and user.role == Role.ALC
    assert user.sbu_id is None and user.dcu_id is None and user.email is None
    assert user.is_active is True and user.must_change_password is True
    cred = next(c for c in credentials if c.alc_code == "10000001")
    assert cred.login_identifier == "10000001" and cred.role == "ALC"
    assert (cred.rcu, cred.dcu, cred.sbu) == ("RCU Pune", "DCU Nashik", "SBU 4")
    assert cred.display_name == "Nashik One"
    assert verify_password(cred.temporary_password, user.password_hash)
    assert not verify_password("wrong-password-123", user.password_hash)
    # Scope: exactly its own ALC, reached through ALC -> SBU -> DCU -> RCU.
    assert has_scope_key(user)
    assert list(await accessible_alc_ids(session, user)) == [alc.id]
    sbu = await session.get(SBU, alc.sbu_id)
    assert sbu.code == "SBU 4" and sbu.dcu_id == world["dcus"]["DCU_NASHIK"].id


# 2, 19: missing SBU user created correctly with SBU scope
@pytest.mark.asyncio
async def test_missing_sbu_user_created_correctly(world):
    session = world["session"]
    _, credentials = await ap.provision(session, "sbu", allow_partial=True)
    await session.commit()
    assert sorted(c.sbu for c in credentials) == ["SBU 4", "SBU 6", "SBU_Pune_North_1"]
    user = await session.scalar(select(User).where(User.username == "sbu-4"))
    assert user.role == Role.SBU and user.sbu_id == world["sbu4"].id
    assert user.alc_id is None and user.dcu_id is None
    assert user.must_change_password is True and user.is_active is True
    cred = next(c for c in credentials if c.sbu == "SBU 4")
    assert cred.login_identifier == "sbu-4" and cred.alc_code == ""
    assert verify_password(cred.temporary_password, user.password_hash)
    expected = {world["alcs"]["10000001"].id, world["alcs"]["10000002"].id}
    assert set(await accessible_alc_ids(session, user)) == expected


# 15: plaintext never stored; audit carries neither password nor hash
@pytest.mark.asyncio
async def test_plaintext_password_not_stored_or_audited(world):
    session = world["session"]
    _, credentials = await ap.provision(session, "alc", allow_partial=True)
    await session.commit()
    rows = await users_snapshot(session)
    audit = (await session.execute(select(AuditLog.audit_metadata, AuditLog.action))).all()
    blob_users = repr(rows)
    blob_audit = repr(audit)
    for cred in credentials:
        assert cred.temporary_password not in blob_users
        assert cred.temporary_password not in blob_audit
    hashes = [r[3] for r in rows]
    assert not any(h in blob_audit for h in hashes)
    created = [m for m, a in audit if a == "user_created"]
    assert len(created) == 4
    assert all(m["method"] == "bulk_provisioning" and m["role"] == "ALC" for m in created)
    summary = next(m for m, a in audit if a == "accounts_provisioned")
    assert summary["created"] == 4


def test_generated_passwords_are_strong_and_unique():
    passwords = {ap.generate_password() for _ in range(200)}
    assert len(passwords) == 200
    for pw in passwords:
        assert len(pw) == 16
        assert any(c.isupper() for c in pw) and any(c.islower() for c in pw)
        assert any(c.isdigit() for c in pw) and any(c in "!#%*?" for c in pw)
        assert pw[0].isalpha()  # never starts with a spreadsheet formula character


# --------------------------------------------------------------------------- #
# Existing accounts
# --------------------------------------------------------------------------- #
# 3, 4, 5: existing ALC / SBU accounts preserved, passwords not replaced
@pytest.mark.asyncio
async def test_existing_accounts_preserved_and_passwords_kept(world):
    session = world["session"]
    session.add_all(
        [
            User(
                username="legacy-name",
                password_hash=hash_password(KNOWN),
                role=Role.ALC,
                alc_id=world["alcs"]["10000001"].id,
                must_change_password=False,
            ),
            User(
                username="sbu4-lead",
                password_hash=hash_password(KNOWN),
                role=Role.SBU,
                sbu_id=world["sbu4"].id,
                must_change_password=False,
            ),
        ]
    )
    await session.commit()
    before = await users_snapshot(session)

    alc_report, alc_creds = await ap.provision(session, "alc", allow_partial=True)
    sbu_report, sbu_creds = await ap.provision(session, "sbu", allow_partial=True)
    await session.commit()

    alc_items, sbu_items = by_key(alc_report), by_key(sbu_report)
    assert alc_items["10000001"]["action"] == "existing"
    assert alc_items["10000001"]["note"] == "non-standard username"
    assert sbu_items["SBU 4"]["action"] == "existing"
    assert "10000001" not in {c.alc_code for c in alc_creds}
    assert "SBU 4" not in {c.sbu for c in sbu_creds}
    after = {r[1]: r for r in await users_snapshot(session)}
    for row in before:
        assert after[row[1]] == row  # every pre-existing user byte-for-byte unchanged
    legacy = await session.scalar(select(User).where(User.username == "legacy-name"))
    assert verify_password(KNOWN, legacy.password_hash)
    assert legacy.must_change_password is False
    assert await count(session, User, User.alc_id == world["alcs"]["10000001"].id) == 1
    assert await count(session, User, User.sbu_id == world["sbu4"].id) == 1


# 6: duplicate login identifier detected safely
@pytest.mark.asyncio
async def test_duplicate_login_identifiers_detected(world):
    session = world["session"]
    session.add_all(
        [
            # The standard ALC username already belongs to someone else.
            User(
                username="alc-10000002", password_hash="x", role=Role.SBU, sbu_id=world["north"].id
            ),
            # A DCU username equal to an ALC Code would make the portal login ambiguous.
            User(
                username="10000003",
                password_hash="x",
                role=Role.DCU,
                dcu_id=world["dcus"]["DCU_PUNE_SOUTH"].id,
            ),
            # The SBU username an SBU would get is already taken.
            User(username="sbu-6", password_hash="x", role=Role.ADMIN),
        ]
    )
    await session.commit()
    alc_items = by_key(await ap.plan(session, "alc"))
    assert alc_items["10000002"]["action"] == "conflict"
    assert "already belongs to 'alc-10000002'" in alc_items["10000002"]["reason"]
    assert alc_items["10000003"]["action"] == "conflict"
    assert "ambiguous login" in alc_items["10000003"]["reason"]
    sbu_items = by_key(await ap.plan(session, "sbu"))
    assert sbu_items["SBU 6"]["action"] == "conflict"


@pytest.mark.asyncio
async def test_two_sbus_producing_the_same_username_conflict(world):
    session = world["session"]
    session.add(SBU(code="sbu_4", name="Look-alike", dcu_id=world["dcus"]["DCU_NASHIK"].id))
    await session.commit()
    items = by_key(await ap.plan(session, "sbu"))
    assert items["SBU 4"]["action"] == items["sbu_4"]["action"] == "conflict"
    assert "would be shared" in items["SBU 4"]["reason"]


# 7: conflicting ALC assignment rejected
@pytest.mark.asyncio
async def test_conflicting_alc_assignments_rejected(world):
    session = world["session"]
    a1, a2, a3 = (world["alcs"][c] for c in ("10000001", "10000002", "10000003"))
    session.add_all(
        [
            User(username="dup-one", password_hash="x", role=Role.ALC, alc_id=a1.id),
            User(username="dup-two", password_hash="x", role=Role.ALC, alc_id=a1.id),
            User(
                username="wrong-role",
                password_hash="x",
                role=Role.SBU,
                alc_id=a2.id,
                sbu_id=world["sbu4"].id,
            ),
            User(
                username="extra-key",
                password_hash="x",
                role=Role.ALC,
                alc_id=a3.id,
                sbu_id=world["sbu6"].id,
            ),
        ]
    )
    await session.commit()
    items = by_key(await ap.plan(session, "alc"))
    assert items["10000001"]["action"] == "conflict"
    assert "duplicate assignment" in items["10000001"]["reason"]
    assert items["10000002"]["action"] == "conflict"
    assert "non-ALC user" in items["10000002"]["reason"]
    assert items["10000003"]["action"] == "conflict"
    assert "SBU/DCU key" in items["10000003"]["reason"]


# 8: conflicting SBU assignment rejected
@pytest.mark.asyncio
async def test_conflicting_sbu_assignments_rejected(world):
    session = world["session"]
    session.add_all(
        [
            User(
                username="alc-in-sbu",
                password_hash="x",
                role=Role.ALC,
                alc_id=world["alcs"]["10000001"].id,
                sbu_id=world["sbu4"].id,
            ),
            User(
                username="sbu6-with-dcu",
                password_hash="x",
                role=Role.SBU,
                sbu_id=world["sbu6"].id,
                dcu_id=world["dcus"]["DCU_NASHIK"].id,
            ),
        ]
    )
    await session.commit()
    items = by_key(await ap.plan(session, "sbu"))
    assert items["SBU 4"]["action"] == "conflict" and "non-SBU" in items["SBU 4"]["reason"]
    assert items["SBU 6"]["action"] == "conflict" and "ALC/DCU key" in items["SBU 6"]["reason"]


@pytest.mark.asyncio
async def test_conflicts_block_execution_unless_partial(world):
    session = world["session"]
    session.add(User(username="alc-10000002", password_hash="x", role=Role.ADMIN))
    await session.commit()
    before = await users_snapshot(session)
    with pytest.raises(ap.ProvisioningBlocked) as exc:
        await ap.provision(session, "alc")
    await session.rollback()
    assert "conflict" in exc.value.reason
    assert await users_snapshot(session) == before
    report, creds = await ap.provision(session, "alc", allow_partial=True)
    await session.commit()
    assert {c.alc_code for c in creds} == {"10000001", "10000003", "20000001"}


@pytest.mark.asyncio
async def test_expect_create_mismatch_blocks(world):
    session = world["session"]
    before = await users_snapshot(session)
    with pytest.raises(ap.ProvisioningBlocked) as exc:
        await ap.provision(session, "alc", allow_partial=True, expect_create=784)
    await session.rollback()
    assert "expected to create 784" in exc.value.reason
    assert await users_snapshot(session) == before


# --------------------------------------------------------------------------- #
# Dry run and idempotency
# --------------------------------------------------------------------------- #
# 16: dry run writes nothing
@pytest.mark.asyncio
async def test_dry_run_makes_no_changes(world, capsys):
    session = world["session"]
    before_users = await users_snapshot(session)
    before_audit = await count(session, AuditLog)
    for role in ("alc", "sbu"):
        code = await cli.run(cli_args(role=role, dry_run=True, execute=False), factory_for(session))
        assert code == 1  # the fixture deliberately has invalid hierarchy records
    output = capsys.readouterr().out
    assert "would create         : 4" in output and "DRY RUN" in output
    assert "$argon2" not in output
    assert await users_snapshot(session) == before_users
    assert await count(session, AuditLog) == before_audit


# 17: second run is idempotent
@pytest.mark.asyncio
async def test_second_run_is_idempotent(world):
    session = world["session"]
    _, first = await ap.provision(session, "alc", allow_partial=True)
    await session.commit()
    snapshot = await users_snapshot(session)
    audits = await count(session, AuditLog)
    report, second = await ap.provision(session, "alc", allow_partial=True)
    await session.commit()
    assert len(first) == 4 and second == []
    assert report["counts"]["create"] == 0 and report["counts"]["existing"] == 4
    assert await users_snapshot(session) == snapshot
    assert await count(session, AuditLog) == audits


# 20: ADMIN / DCU accounts are never created
@pytest.mark.asyncio
async def test_admin_and_dcu_accounts_never_created(world):
    session = world["session"]
    for role in ("admin", "dcu", "ADMIN", "DCU"):
        with pytest.raises(ValueError):
            await ap.plan(session, role)
    for role in ("admin", "dcu"):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["--role", role, "--dry-run"])
    admins = await count(session, User, User.role == Role.ADMIN)
    dcus = await count(session, User, User.role == Role.DCU)
    await ap.provision(session, "alc", allow_partial=True)
    await ap.provision(session, "sbu", allow_partial=True)
    await session.commit()
    assert await count(session, User, User.role == Role.ADMIN) == admins
    assert await count(session, User, User.role == Role.DCU) == dcus


# --------------------------------------------------------------------------- #
# CLI execution and credential file
# --------------------------------------------------------------------------- #
def test_cli_requires_explicit_mode_and_execute_arguments(monkeypatch):
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--role", "alc"])  # neither --dry-run nor --execute
    with pytest.raises(SystemExit):
        parser.parse_args(["--role", "alc", "--dry-run", "--execute"])
    monkeypatch.setattr("sys.argv", ["provision_accounts", "--role", "alc", "--execute"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2  # --out and --expect-create are mandatory with --execute


@pytest.mark.asyncio
async def test_cli_execute_writes_private_credential_file(world, tmp_path, capsys):
    session = world["session"]
    out = tmp_path / "alc-accounts.csv"
    code = await cli.run(cli_args(expect_create=4, out=out), factory_for(session))
    assert code == 0
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert not Path(f"{out}.partial").exists()
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert list(rows[0]) == list(cli.OUTPUT_COLUMNS)
    assert len(rows) == 4
    north = next(r for r in rows if r["ALC Code"] == "20000001")
    assert north["Display Name"] == "'=North Centre"  # formula injection neutralised
    assert north["Login Identifier"] == "20000001" and north["Role"] == "ALC"
    for row in rows:
        user = await session.scalar(select(User).where(User.username == row["Username"]))
        assert verify_password(row["Temporary Password"], user.password_hash)
        assert user.password_hash not in out.read_text()
    printed = capsys.readouterr().out
    assert "$argon2" not in printed
    assert not any(r["Temporary Password"] in printed for r in rows)

    # Re-running refuses to overwrite the credential file and creates nothing.
    assert await cli.run(cli_args(expect_create=0, out=out), factory_for(session)) == 2


@pytest.mark.asyncio
async def test_cli_refuses_non_ignored_path_inside_repository(world):
    out = REPO / "provisioning-output-test.csv"
    before = await users_snapshot(world["session"])
    code = await cli.run(cli_args(expect_create=4, out=out), factory_for(world["session"]))
    assert code == 2 and not out.exists()
    assert await users_snapshot(world["session"]) == before


@pytest.mark.asyncio
async def test_cli_failure_rolls_back_and_removes_partial_file(world, tmp_path, monkeypatch):
    session = world["session"]
    before = await users_snapshot(session)
    out = tmp_path / "creds.csv"

    async def failing_commit():
        raise RuntimeError("database went away")

    monkeypatch.setattr(session, "commit", failing_commit)
    with pytest.raises(RuntimeError):
        await cli.run(cli_args(expect_create=4, out=out), factory_for(session))
    monkeypatch.undo()
    assert not out.exists() and not Path(f"{out}.partial").exists()
    assert await users_snapshot(session) == before


@pytest.mark.asyncio
async def test_cli_expect_create_mismatch_writes_nothing(world, tmp_path):
    out = tmp_path / "creds.csv"
    before = await users_snapshot(world["session"])
    assert await cli.run(cli_args(expect_create=5, out=out), factory_for(world["session"])) == 1
    assert not out.exists() and not Path(f"{out}.partial").exists()
    assert await users_snapshot(world["session"]) == before


# --------------------------------------------------------------------------- #
# End to end: a provisioned ALC logs in with its ALC Code and must change password
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_provisioned_alc_logs_in_with_alc_code_and_must_change_password(
    client, session, seeded
):
    await ensure_hierarchy(session)
    session.add(ALC(alc_code="57219999", alc_name="Fresh Centre", sbu_id=seeded["sbu4"].id))
    await session.commit()
    report, creds = await ap.provision(session, "alc")
    await session.commit()
    assert report["counts"]["existing"] == 3  # conftest's Centre A/B/C logins kept
    (cred,) = creds
    response = await login(client, "57219999", cred.temporary_password, "ALC")
    assert response.status_code == 200
    assert response.json()["must_change_password"] is True
    blocked = await client.get("/api/portal/dashboard")
    assert blocked.status_code == 403 and blocked.json()["detail"] == "Password change required"
    wrong = await login(client, "57219999", "not-the-password-1", "ALC")
    assert wrong.status_code == 401
