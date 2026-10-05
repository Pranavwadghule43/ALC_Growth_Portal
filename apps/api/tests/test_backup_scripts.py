"""Behaviour of the backup / restore shell scripts (apps/api/scripts/backup/*.sh).

PostgreSQL client programs are replaced by small stubs on PATH that record how they were
called, so the safety guards and the orchestration are tested without a database. Real
PostgreSQL and MinIO restores are drills (docs/BACKUP_AND_RECOVERY.md), not unit tests.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_backup_tool import fake_set

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "backup"
ENTRY_POINTS = ["backup.sh", "verify_backup.sh", "restore_postgres.sh", "restore_storage.sh",
                "retention.sh"]
PASSWORD = "stub-password-must-never-be-printed"

# The scripts target the Ubuntu server; on Windows they are exercised in the restore drill.
pytestmark = pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None,
                                reason="POSIX bash is required")

STUB = r'''#!{python}
"""Stub for {name}: records the call, answers like the real program would."""
import os, sys
name = {name!r}
args = sys.argv[1:]
stdin = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(name + " " + " ".join(args) + "\n")
if os.environ.get("STUB_FAIL") == name:
    sys.exit(3)
text = " ".join(args) + " " + stdin
if name == "pg_dump":
    target = next(a.split("=", 1)[1] for a in args if a.startswith("--file="))
    open(target, "wb").write(b"PGDMP stub dump")
elif name == "pg_restore":
    files = [a for a in args if not a.startswith("-")]
    if files and not os.path.exists(files[-1]):
        sys.exit(1)
    if "--table=alembic_version" in args:
        print("COPY public.alembic_version (version_num) FROM stdin;")
        print(os.environ.get("STUB_ALEMBIC", "20261002_0007"))
        print("\\.")
elif name == "psql":
    if "version_num" in text:
        if os.environ.get("STUB_NO_ALEMBIC"):
            sys.exit(1)
        print(os.environ.get("STUB_ALEMBIC", "20261002_0007"))
    elif "server_version" in text:
        print("16.14")
    elif "pg_database" in text:
        print(os.environ.get("STUB_DB_EXISTS", "1"))
    elif "pg_class" in text:
        print(os.environ.get("STUB_TABLES", "17"))
    elif "to_regclass" in text:
        print("t")
    elif "count(*)" in text:
        print("5")
'''


@pytest.fixture
def env(tmp_path):
    """Environment with stubbed PostgreSQL tools and local evidence storage."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("psql", "pg_dump", "pg_restore", "createdb", "dropdb"):
        path = bin_dir / name
        path.write_text(STUB.format(python=sys.executable, name=name))
        path.chmod(0o755)
    store = tmp_path / "store"
    (store / "evidence" / "alc" / "act").mkdir(parents=True)
    (store / "evidence" / "alc" / "act" / "proof.pdf").write_bytes(b"%PDF-1.4 proof")
    root = tmp_path / "backups"
    values = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "STUB_LOG": str(tmp_path / "calls.log"),
        "BACKUP_PYTHON": sys.executable,
        "BACKUP_ROOT": str(root),
        "PGHOST": "127.0.0.1", "PGPORT": "55432", "PGUSER": "alc", "PGDATABASE": "alc_growth",
        "PGPASSWORD": PASSWORD,
        "STORAGE_BACKEND": "local", "LOCAL_STORAGE_PATH": str(store),
        "HOME": str(tmp_path),
    }
    (tmp_path / "calls.log").write_text("")
    return {"vars": values, "root": root, "log": tmp_path / "calls.log", "tmp": tmp_path}


def run(env, script, *args, **overrides):
    variables = {**env["vars"], **overrides}
    variables = {k: v for k, v in variables.items() if v is not None}
    result = subprocess.run(["bash", str(SCRIPTS / script), *args], env=variables,
                            capture_output=True, text=True, timeout=120)
    assert PASSWORD not in result.stdout + result.stderr
    return result


def calls(env, program=None):
    lines = env["log"].read_text().splitlines()
    return [line for line in lines if program is None or line.split(" ", 1)[0] == program]


# --------------------------------------------------------------------------- #
# Static checks
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", [*ENTRY_POINTS, "common.sh"])
def test_scripts_are_defensive_bash(name):
    text = (SCRIPTS / name).read_text()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert subprocess.run(["bash", "-n", str(SCRIPTS / name)]).returncode == 0
    assert "eval " not in code and "rm -rf" not in code and "set -x" not in code
    if name != "common.sh":
        assert text.startswith("#!/usr/bin/env bash\n")
        assert "set -euo pipefail" in text
        assert os.stat(SCRIPTS / name).st_mode & stat.S_IXUSR


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck is not installed")
def test_shellcheck_is_clean():
    result = subprocess.run(["shellcheck", "-x", "-s", "bash", *ENTRY_POINTS, "common.sh"],
                            cwd=SCRIPTS, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout


# --------------------------------------------------------------------------- #
# backup.sh
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("root", ["/", "/home", "/etc", "relative/backups", ".", " "])
def test_backup_rejects_dangerous_roots(env, root):
    result = run(env, "backup.sh", BACKUP_ROOT=root)
    assert result.returncode != 0 and "BACKUP_ROOT" in result.stderr
    assert calls(env) == []


def test_backup_rejects_incomplete_or_url_configuration(env):
    assert run(env, "backup.sh", BACKUP_ROOT=None).returncode != 0
    assert run(env, "backup.sh", PGDATABASE=None).returncode != 0
    result = run(env, "backup.sh", PGHOST="postgresql://alc:secret@db/alc_growth")
    assert result.returncode != 0 and "not a connection string" in result.stderr
    assert "secret" not in result.stderr
    assert calls(env) == []


def test_backup_creates_a_complete_verified_set(env):
    result = run(env, "backup.sh")
    assert result.returncode == 0, result.stderr
    (set_dir,) = [p for p in env["root"].iterdir() if p.is_dir()]
    manifest = json.loads((set_dir / "manifest.json").read_text())
    assert manifest["status"] == "complete" and not (set_dir / "INCOMPLETE").exists()
    assert manifest["database"]["alembic_revision"] == "20261002_0007"
    assert manifest["storage"]["object_count"] == 1
    stored = set_dir / "storage/objects/evidence/alc/act/proof.pdf"
    assert stored.read_bytes() == b"%PDF-1.4 proof"
    assert any("--format=custom" in c and "--no-password" in c for c in calls(env, "pg_dump"))
    assert any("--list" in c for c in calls(env, "pg_restore"))
    for message in ("backup started", "database dump completed", "storage backup completed",
                    "verification completed", "backup finished"):
        assert message in result.stderr
    assert (env["root"].stat().st_mode & 0o777) == 0o700
    assert run(env, "verify_backup.sh", str(set_dir)).returncode == 0


def test_failed_backup_is_marked_incomplete(env):
    result = run(env, "backup.sh", STUB_FAIL="pg_dump")
    assert result.returncode != 0 and "backup FAILED at step 'database dump'" in result.stderr
    (set_dir,) = [p for p in env["root"].iterdir() if p.is_dir()]
    assert "failed_step=database dump" in (set_dir / "INCOMPLETE").read_text()
    assert not (set_dir / "manifest.json").exists()
    assert run(env, "verify_backup.sh", str(set_dir)).returncode != 0


def test_backup_fails_when_storage_is_unavailable(env):
    result = run(env, "backup.sh", LOCAL_STORAGE_PATH=str(env["tmp"] / "missing"))
    assert result.returncode != 0 and "step 'storage backup'" in result.stderr
    (set_dir,) = [p for p in env["root"].iterdir() if p.is_dir()]
    assert (set_dir / "INCOMPLETE").exists() and not (set_dir / "manifest.json").exists()


def test_concurrent_runs_are_refused_flock(env):
    if shutil.which("flock") is None:
        pytest.skip("flock is not installed")
    import fcntl

    env["root"].mkdir(mode=0o700)
    with open(env["root"] / ".backup.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for script in ("backup.sh", "retention.sh"):
            result = run(env, script)
            assert result.returncode == 75 and "refusing to start" in result.stderr
    assert calls(env) == [] and not any(env["root"].glob("2*"))


def test_concurrent_runs_are_refused_mkdir_lock(env):
    env["root"].mkdir(mode=0o700)
    (env["root"] / ".backup.lock.d").mkdir()
    result = run(env, "backup.sh", BACKUP_LOCK_METHOD="mkdir")
    assert result.returncode == 75 and calls(env) == []
    (env["root"] / ".backup.lock.d").rmdir()
    assert run(env, "backup.sh", BACKUP_LOCK_METHOD="mkdir").returncode == 0
    assert not (env["root"] / ".backup.lock.d").exists()  # released on exit


# --------------------------------------------------------------------------- #
# restore_postgres.sh
# --------------------------------------------------------------------------- #
@pytest.fixture
def dump(env):
    path = env["tmp"] / "alc_growth_db_20261005T090000Z.dump"
    path.write_bytes(b"PGDMP stub dump")
    return path


def restored(env):
    return [c for c in calls(env, "pg_restore") if "--list" not in c and "--table=" not in c]


def test_restore_without_confirmation_is_refused(env, dump):
    result = run(env, "restore_postgres.sh", str(dump))
    assert result.returncode == 1 and "Restoring would DESTROY it" in result.stderr
    assert "host=127.0.0.1 port=55432 database=alc_growth user=alc" in result.stderr
    assert calls(env, "dropdb") == [] and restored(env) == []


@pytest.mark.parametrize("confirm", [None, "YES", "yes", "alc_growth_other"])
def test_force_needs_the_database_name_as_confirmation(env, dump, confirm):
    result = run(env, "restore_postgres.sh", "--force", str(dump), RESTORE_CONFIRM=confirm)
    assert result.returncode == 1 and "RESTORE_CONFIRM=alc_growth" in result.stderr
    assert calls(env, "dropdb") == [] and restored(env) == []


def test_confirmation_without_force_is_refused(env, dump):
    result = run(env, "restore_postgres.sh", str(dump), RESTORE_CONFIRM="alc_growth")
    assert result.returncode == 1 and calls(env, "dropdb") == []


def test_confirmed_destructive_restore_replaces_the_database(env, dump):
    result = run(env, "restore_postgres.sh", "--force", str(dump), RESTORE_CONFIRM="alc_growth")
    assert result.returncode == 0, result.stderr
    assert "DESTRUCTIVE" in result.stderr
    assert calls(env, "dropdb") and calls(env, "createdb")
    (restore_call,) = restored(env)
    for flag in ("--no-owner", "--no-privileges", "--exit-on-error", "--single-transaction",
                 "--dbname=alc_growth"):
        assert flag in restore_call


def test_restore_into_an_empty_database_needs_no_confirmation(env, dump):
    result = run(env, "restore_postgres.sh", str(dump), STUB_TABLES="0")
    assert result.returncode == 0, result.stderr
    assert "restore mode: empty database" in result.stderr
    assert calls(env, "dropdb") == [] and calls(env, "createdb") == [] and restored(env)


def test_missing_database_requires_create(env, dump):
    result = run(env, "restore_postgres.sh", str(dump), STUB_DB_EXISTS="0")
    assert result.returncode == 1 and "does not exist" in result.stderr
    assert restored(env) == []
    result = run(env, "restore_postgres.sh", "--create", str(dump), STUB_DB_EXISTS="0",
                 RESTORE_DB_OWNER="alc", RESTORE_ROLE="alc")
    assert result.returncode == 0, result.stderr
    assert any("--owner=alc" in c for c in calls(env, "createdb"))
    assert "--role=alc" in restored(env)[0]


def test_restore_missing_dump_is_refused(env):
    result = run(env, "restore_postgres.sh", str(env["tmp"] / "missing.dump"))
    assert result.returncode == 1 and "backup not found" in result.stderr
    assert calls(env, "psql") == []


@pytest.mark.parametrize("database", ["postgres", "template0", "template1"])
def test_system_databases_are_never_restore_targets(env, dump, database):
    result = run(env, "restore_postgres.sh", "--force", str(dump), PGDATABASE=database,
                 RESTORE_CONFIRM=database)
    assert result.returncode == 1 and "system database" in result.stderr
    assert calls(env) == []


def test_restore_from_a_set_checks_the_alembic_revision(env):
    assert run(env, "backup.sh").returncode == 0
    (set_dir,) = [p for p in env["root"].iterdir() if p.is_dir()]
    ok = run(env, "restore_postgres.sh", str(set_dir), STUB_TABLES="0")
    assert ok.returncode == 0 and "alembic revision 20261002_0007" in ok.stderr
    # The restored database reports another revision than the backup recorded.
    bad = run(env, "restore_postgres.sh", str(set_dir), STUB_TABLES="0",
              STUB_ALEMBIC="20260101_0000")
    assert bad.returncode == 1 and "does not match the backup (20261002_0007)" in bad.stderr
    (set_dir / "manifest.json").write_text("{}")
    tampered = run(env, "restore_postgres.sh", str(set_dir), STUB_TABLES="0")
    assert tampered.returncode == 1 and "failed verification" in tampered.stderr


def test_restored_database_must_have_alembic_version(env, dump):
    result = run(env, "restore_postgres.sh", str(dump), STUB_TABLES="0", STUB_NO_ALEMBIC="1")
    assert result.returncode == 1 and "alembic_version is missing" in result.stderr


def test_check_mode_reports_counts(env):
    result = run(env, "restore_postgres.sh", "--check")
    assert result.returncode == 0
    assert "activity_evidence" in result.stdout and "alembic revision" in result.stderr
    assert calls(env, "dropdb") == [] and restored(env) == []


def test_verify_tables_must_be_identifiers(env):
    result = run(env, "restore_postgres.sh", "--check", RESTORE_VERIFY_TABLES="users;drop")
    assert result.returncode == 1 and "invalid table name" in result.stderr


# --------------------------------------------------------------------------- #
# retention.sh / restore_storage.sh / verify_backup.sh
# --------------------------------------------------------------------------- #
def test_retention_dry_run_then_apply(env):
    env["root"].mkdir(mode=0o700)
    for set_id in ("20261001T020000Z", "20261001T030000Z", "20261002T020000Z"):
        fake_set(env["root"], set_id)
    dry = run(env, "retention.sh")
    assert dry.returncode == 0 and "would delete 20261001T020000Z" in dry.stderr
    assert len(list(env["root"].glob("2*"))) == 3
    applied = run(env, "retention.sh", "--apply")
    assert applied.returncode == 0
    remaining = sorted(p.name for p in env["root"].glob("2*"))
    assert remaining == ["20261001T030000Z", "20261002T020000Z"]


def test_retention_rejects_dangerous_roots(env):
    for root in ("/", "/home", ""):
        result = run(env, "retention.sh", "--apply", BACKUP_ROOT=root)
        assert result.returncode != 0


def test_restore_storage_requires_a_backup_set(env):
    result = run(env, "restore_storage.sh", str(env["tmp"] / "missing"))
    assert result.returncode == 1 and "backup set not found" in result.stderr
    assert run(env, "restore_storage.sh").returncode == 2


def test_restore_storage_from_a_set_never_deletes(env):
    assert run(env, "backup.sh").returncode == 0
    (set_dir,) = [p for p in env["root"].iterdir() if p.is_dir()]
    target = env["tmp"] / "restored"
    (target / "other").mkdir(parents=True)
    (target / "other" / "keep.txt").write_text("keep")
    result = run(env, "restore_storage.sh", str(set_dir), LOCAL_STORAGE_PATH=str(target))
    assert result.returncode == 0, result.stderr
    assert (target / "evidence/alc/act/proof.pdf").read_bytes() == b"%PDF-1.4 proof"
    assert (target / "other/keep.txt").read_text() == "keep"


def test_verify_usage(env):
    assert run(env, "verify_backup.sh").returncode == 2
    assert run(env, "verify_backup.sh", str(env["tmp"] / "missing")).returncode == 1
