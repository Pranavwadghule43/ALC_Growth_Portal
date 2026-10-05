"""Backup / restore helper (apps/api/scripts/backup/backup_tool.py).

No database or object store is needed: pg_restore is replaced where the dump is read, and S3 is
an in-memory fake with the same calls the helper makes (list_objects_v2, get_object,
head_object, put_object).
"""
import hashlib
import importlib.util
import io
import json
import os
import sys
from pathlib import Path

import pytest

TOOL_PATH = Path(__file__).resolve().parents[1] / "scripts" / "backup" / "backup_tool.py"
_spec = importlib.util.spec_from_file_location("backup_tool", TOOL_PATH)
bt = importlib.util.module_from_spec(_spec)
sys.modules["backup_tool"] = bt
_spec.loader.exec_module(bt)

SECRETS = {
    "S3_ACCESS_KEY": "AKIA-test-access-key-value",
    "S3_SECRET_KEY": "test-secret-key-value-do-not-leak",
    "PGPASSWORD": "pg-password-do-not-leak",
    "SECRET_KEY": "app-secret-key-do-not-leak-0123456789",
    "DATABASE_URL": "postgresql+asyncpg://alc:pg-password-do-not-leak@db:5432/alc_growth",
    "REDIS_URL": "redis://:redis-password-do-not-leak@127.0.0.1:6379/0",
}


# --------------------------------------------------------------------------- #
# Backup root
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw", [
    "", " ", "/", ".", "..", "backups", "./backups", "/home", "/home/pranav", "/root", "/etc",
    "/var", "/var/lib", "/tmp", "/usr", "/opt", "/srv", "/mnt", "/var/backups",
    "/srv/backups/../../etc", "/srv/./backups", " /srv/backups", "/srv/backups\n",
])
def test_dangerous_posix_roots_are_rejected(raw):
    assert bt.root_problem(raw, windows=False) is not None


@pytest.mark.parametrize("raw", [
    "C:\\", "C:/", "C:", "D:\\", "\\\\server\\share", "C:\\Windows", "c:\\windows",
    "C:\\Users", "C:\\Users\\Pranav", "C:\\Program Files", "C:\\ProgramData",
    "C:\\Backups\\..\\Windows", "backups",
])
def test_dangerous_windows_roots_are_rejected(raw):
    assert bt.root_problem(raw, windows=True) is not None


@pytest.mark.parametrize("raw", [
    "/srv/alc-growth/backups", "/var/backups/alc-growth", "/mnt/backup/alc-growth",
    "/home/backup/alc-growth",
])
def test_dedicated_posix_roots_are_accepted(raw):
    assert bt.root_problem(raw, windows=False) is None


@pytest.mark.parametrize("raw", ["D:\\Backups\\alc-growth", "C:\\Backups\\alc"])
def test_dedicated_windows_roots_are_accepted(raw):
    assert bt.root_problem(raw, windows=True) is None


def test_repository_and_its_parents_are_rejected():
    repo = bt.repository_root()
    assert bt.root_problem(str(repo), windows=False) is not None
    assert bt.root_problem(str(repo.parent), windows=False) is not None


def test_validate_root_creates_a_private_directory(tmp_path):
    root = tmp_path / "backups"
    with pytest.raises(bt.BackupError, match="does not exist"):
        bt.validate_root(str(root))
    assert bt.validate_root(str(root), create=True) == root
    assert root.is_dir()
    if os.name != "nt":  # Windows has no POSIX mode bits
        assert (root.stat().st_mode & 0o777) == 0o700


POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")


@POSIX_ONLY
def test_validate_root_rejects_files_and_symlinks(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    (tmp_path / "link").symlink_to(target)
    (tmp_path / "file").write_text("x")
    for name in ("link", "file"):
        with pytest.raises(bt.BackupError):
            bt.validate_root(str(tmp_path / name))


@POSIX_ONLY
def test_validate_root_rejects_symlink_parents_resolving_to_system_dirs(tmp_path):
    (tmp_path / "etc-link").symlink_to("/etc")
    with pytest.raises(bt.BackupError):
        bt.validate_root(str(tmp_path / "etc-link"))


def test_check_root_command_rejects_without_printing_a_path(capsys):
    assert bt.main(["check-root", "/"]) == 1
    assert capsys.readouterr().out == ""


# --------------------------------------------------------------------------- #
# Names, keys and sets
# --------------------------------------------------------------------------- #
def test_dump_filename_is_safe_and_timestamped():
    expected = "alc_growth_db_20261005T090000Z.dump"
    assert bt.dump_filename("alc_growth", "20261005T090000Z") == expected
    name = bt.dump_filename("alc growth; rm -rf /", "20261005T090000Z")
    assert name == "alc_growth_rm_-rf_db_20261005T090000Z.dump"  # only [A-Za-z0-9_-] remain
    assert bt.dump_filename("../..", "20261005T090000Z") == "database_db_20261005T090000Z.dump"
    for bad in ("2026-10-05", "20261005T090000", "../20261005T090000Z", ""):
        with pytest.raises(bt.BackupError):
            bt.dump_filename("alc_growth", bad)


def test_begin_set_creates_an_incomplete_set(tmp_path):
    set_dir, dump = bt.begin_set(tmp_path, "alc_growth")
    assert bt.SET_ID_RE.match(set_dir.name)
    assert (set_dir / "INCOMPLETE").is_file()
    assert dump.parent == set_dir / "database" and dump.name.endswith(f"_{set_dir.name}.dump")
    assert bt.verify_set(set_dir)  # not valid until finalized


@pytest.mark.parametrize("key", [
    "", "/abs", "a/../b", "../x", "a//b", "a/./b", "dir/", "a\\b", "c:x", "a\x00b", "a\nb",
])
def test_unsafe_object_keys_are_rejected(tmp_path, key):
    assert bt.key_problem(key)
    with pytest.raises(bt.BackupError):
        bt.object_path(tmp_path, key)


def test_application_keys_are_accepted(tmp_path):
    key = "evidence/7f1c/9a2b/4e5d-6f70.pdf"
    expected = tmp_path / "evidence" / "7f1c" / "9a2b" / "4e5d-6f70.pdf"
    assert bt.object_path(tmp_path, key) == expected


# --------------------------------------------------------------------------- #
# A complete set (local storage backend, dump reading stubbed)
# --------------------------------------------------------------------------- #
EVIDENCE = {
    "evidence/alc-1/act-1/a.pdf": b"%PDF-1.4 first",
    "evidence/alc-1/act-1/b.png": b"\x89PNG second",
    "evidence/alc-2/act-9/c.webp": b"RIFF....WEBP third",
}


@pytest.fixture
def fake_dump(monkeypatch):
    state = {"revision": "20261002_0007", "keys": sorted(EVIDENCE), "readable": True}
    monkeypatch.setattr(bt, "pg_restore_lists", lambda dump: state["readable"])
    monkeypatch.setattr(bt, "dump_alembic_revision", lambda dump: state["revision"])
    monkeypatch.setattr(bt, "dump_evidence_keys",
                        lambda dump: (len(state["keys"]) + 1, state["keys"]))
    return state


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    store = tmp_path / "evidence-store"
    for key, body in EVIDENCE.items():
        path = store.joinpath(*key.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(store))
    return store


def make_complete_set(root: Path) -> Path:
    set_dir, dump = bt.begin_set(root, "alc_growth")
    dump.write_bytes(b"PGDMP fake custom-format dump " * 50)
    bt.storage_backup(set_dir, "local")
    bt.finalize(set_dir, dump=dump, database_name="alc_growth", server_version="16.14",
                pg_dump_version="pg_dump (PostgreSQL) 16.14", app_commit="abc123")
    return set_dir


@pytest.fixture
def complete_set(tmp_path, fake_dump, local_store):
    root = tmp_path / "backups"
    root.mkdir()
    return make_complete_set(root)


def test_complete_set_verifies(complete_set):
    assert not (complete_set / "INCOMPLETE").exists()
    assert bt.verify_set(complete_set) == []
    manifest = json.loads((complete_set / "manifest.json").read_text())
    assert manifest["status"] == "complete"
    assert manifest["database"]["alembic_revision"] == "20261002_0007"
    assert manifest["storage"]["object_count"] == 3
    assert manifest["references"] == {
        "evidence_rows": 4, "active_evidence_rows": 3, "active_evidence_missing_objects": 0
    }
    assert "not atomically" in manifest["consistency"]


def test_storage_backup_preserves_exact_keys(complete_set):
    inventory = json.loads((complete_set / "storage" / "inventory.json").read_text())
    assert [entry["key"] for entry in inventory["objects"]] == sorted(EVIDENCE)
    for key, body in EVIDENCE.items():
        stored = complete_set / "storage" / "objects" / key
        assert stored.read_bytes() == body


def test_manifest_and_inventory_contain_no_secrets(tmp_path, fake_dump, local_store, monkeypatch):
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    root = tmp_path / "backups"
    root.mkdir()
    set_dir = make_complete_set(root)
    text = "".join((set_dir / name).read_text()
                   for name in ("manifest.json", "storage/inventory.json"))
    for value in SECRETS.values():
        assert value not in text
    for word in ("password", "secret", "postgresql+asyncpg", "redis://", "token"):
        assert word not in text.lower()


def test_missing_dump_is_rejected(complete_set):
    next((complete_set / "database").glob("*.dump")).unlink()
    assert "database dump file is missing" in bt.verify_set(complete_set)


def test_bad_dump_checksum_is_rejected(complete_set):
    dump = next((complete_set / "database").glob("*.dump"))
    data = bytearray(dump.read_bytes())
    data[10] ^= 0xFF  # same size, different content
    dump.write_bytes(bytes(data))
    assert "database dump SHA256 does not match the manifest" in bt.verify_set(complete_set)


def test_unreadable_dump_is_rejected(complete_set, fake_dump):
    fake_dump["readable"] = False
    assert "pg_restore cannot read the database dump" in bt.verify_set(complete_set)


def test_tampered_missing_and_extra_objects_are_rejected(complete_set):
    objects = complete_set / "storage" / "objects"
    (objects / "evidence/alc-1/act-1/a.pdf").write_bytes(b"%PDF-1.4 FIRST")
    (objects / "evidence/alc-1/act-1/b.png").unlink()
    (objects / "evidence/stray.txt").write_bytes(b"x")
    problems = bt.verify_set(complete_set)
    assert "stored object SHA256 differs: evidence/alc-1/act-1/a.pdf" in problems
    assert "stored object missing: evidence/alc-1/act-1/b.png" in problems
    assert "1 unexpected file(s) in storage/objects" in problems


def test_tampered_inventory_or_manifest_is_rejected(complete_set):
    inventory = complete_set / "storage" / "inventory.json"
    inventory.write_text(inventory.read_text().replace('"size"', '"size" ', 1))
    assert "storage inventory SHA256 does not match the manifest" in bt.verify_set(complete_set)
    (complete_set / "manifest.json").write_text("{not json")
    assert bt.verify_set(complete_set)


def test_incomplete_or_unfinished_sets_are_rejected(complete_set):
    (complete_set / "INCOMPLETE").write_text("failed_step=storage backup\n")
    assert "set is marked INCOMPLETE" in bt.verify_set(complete_set)
    (complete_set / "INCOMPLETE").unlink()
    manifest = json.loads((complete_set / "manifest.json").read_text())
    manifest["status"] = "running"
    (complete_set / "manifest.json").write_text(json.dumps(manifest))
    assert any("not 'complete'" in p for p in bt.verify_set(complete_set))


def test_finalize_never_marks_an_unreadable_dump_complete(tmp_path, fake_dump, local_store):
    fake_dump["readable"] = False
    root = tmp_path / "backups"
    root.mkdir()
    set_dir, dump = bt.begin_set(root, "alc_growth")
    dump.write_bytes(b"not a dump")
    bt.storage_backup(set_dir, "local")
    with pytest.raises(bt.BackupError):
        bt.finalize(set_dir, dump=dump, database_name="alc_growth", server_version=None,
                    pg_dump_version=None, app_commit=None)
    assert (set_dir / "INCOMPLETE").exists() and not (set_dir / "manifest.json").exists()


def test_missing_referenced_objects_are_reported(tmp_path, fake_dump, local_store):
    fake_dump["keys"] = [*sorted(EVIDENCE), "evidence/alc-3/act-3/gone.pdf"]
    root = tmp_path / "backups"
    root.mkdir()
    manifest = json.loads((make_complete_set(root) / "manifest.json").read_text())
    assert manifest["references"]["active_evidence_missing_objects"] == 1
    assert manifest["warnings"]


def test_verify_command_exit_status(complete_set):
    assert bt.main(["verify", str(complete_set)]) == 0
    (complete_set / "manifest.json").unlink()
    assert bt.main(["verify", str(complete_set)]) == 1


def test_describe_refuses_an_unverified_set(complete_set, capsys):
    assert bt.main(["describe", str(complete_set)]) == 0
    out = capsys.readouterr().out
    assert "alembic_revision=20261002_0007" in out and ".dump" in out
    next((complete_set / "database").glob("*.dump")).write_bytes(b"truncated")
    assert bt.main(["describe", str(complete_set)]) == 1


# --------------------------------------------------------------------------- #
# Dump reading (pg_restore output parsing)
# --------------------------------------------------------------------------- #
EVIDENCE_COLUMNS = ("id, activity_id, storage_key, original_filename, mime_type, file_size, "
                    "uploaded_by, uploaded_at, is_active")
COPY_OUTPUT = f"""--
-- PostgreSQL database dump
--
COPY public.activity_evidence ({EVIDENCE_COLUMNS}) FROM stdin;
1\ta\tevidence/a/1.pdf\tone.pdf\tapplication/pdf\t10\t\\N\t2026-10-01\tt
2\ta\tevidence/a/2.pdf\ttwo\\tname.pdf\tapplication/pdf\t10\t\\N\t2026-10-01\tf
3\tb\tevidence/b/3.png\tthree.png\timage/png\t10\tu\t2026-10-01\tt
\\.
"""


def test_dump_reading_parses_copy_data(monkeypatch):
    class Result:
        returncode, stdout = 0, COPY_OUTPUT

    monkeypatch.setattr(bt.subprocess, "run", lambda *a, **k: Result)
    columns, rows = bt.dump_table(Path("x.dump"), "activity_evidence")
    assert columns[2] == "storage_key" and rows[1][3] == "two\tname.pdf" and rows[0][6] is None
    assert bt.dump_evidence_keys(Path("x.dump")) == (3, ["evidence/a/1.pdf", "evidence/b/3.png"])


def test_dump_alembic_revision(monkeypatch):
    class Result:
        returncode = 0
        stdout = "COPY public.alembic_version (version_num) FROM stdin;\n20261002_0007\n\\.\n"

    monkeypatch.setattr(bt.subprocess, "run", lambda *a, **k: Result)
    assert bt.dump_alembic_revision(Path("x.dump")) == "20261002_0007"


# --------------------------------------------------------------------------- #
# S3 (in-memory fake)
# --------------------------------------------------------------------------- #
class FakeS3:
    def __init__(self, objects=None, *, etag_override=None):
        self.objects = {key: (body, "application/octet-stream")
                        for key, body in (objects or {}).items()}
        self.etag_override = etag_override or {}
        self.puts = []

    def _etag(self, key):
        return self.etag_override.get(key) or f'"{hashlib.md5(self.objects[key][0]).hexdigest()}"'

    def get_paginator(self, name):
        fake = self

        class Paginator:
            def paginate(self, Bucket):
                yield {"Contents": [{"Key": k, "Size": len(v[0]), "ETag": fake._etag(k)}
                                    for k, v in sorted(fake.objects.items())]}
        return Paginator()

    def get_object(self, Bucket, Key):
        body, content_type = self.objects[Key]
        return {"Body": io.BytesIO(body), "ContentLength": len(body), "ETag": self._etag(Key),
                "ContentType": content_type}

    def head_object(self, Bucket, Key):
        body, content_type = self.objects[Key]
        return {"ContentLength": len(body), "ETag": self._etag(Key), "ContentType": content_type}

    def put_object(self, Bucket, Key, Body, ContentLength, ContentType=None):
        self.puts.append(Key)
        self.objects[Key] = (Body.read(), ContentType or "binary/octet-stream")


@pytest.fixture
def s3_env(monkeypatch):
    for name, value in {"S3_BUCKET": "alc-evidence", "S3_ACCESS_KEY": "k",
                        "S3_SECRET_KEY": "s", "S3_ENDPOINT_URL": "http://127.0.0.1:9000"}.items():
        monkeypatch.setenv(name, value)


def s3_set(tmp_path, client, fake_dump):
    root = tmp_path / "backups"
    root.mkdir(exist_ok=True)
    set_dir, dump = bt.begin_set(root, "alc_growth")
    dump.write_bytes(b"PGDMP fake")
    bt.storage_backup(set_dir, "s3", client=client)
    bt.finalize(set_dir, dump=dump, database_name="alc_growth", server_version=None,
                pg_dump_version=None, app_commit=None)
    return set_dir


def test_s3_backup_keeps_keys_and_content_types(tmp_path, s3_env, fake_dump):
    source = FakeS3(EVIDENCE)
    pdf = "evidence/alc-1/act-1/a.pdf"
    source.objects[pdf] = (EVIDENCE[pdf], "application/pdf")
    set_dir = s3_set(tmp_path, source, fake_dump)
    inventory = json.loads((set_dir / "storage/inventory.json").read_text())
    assert [e["key"] for e in inventory["objects"]] == sorted(EVIDENCE)
    assert inventory["etag_md5_checked"] == 3
    assert inventory["objects"][0]["content_type"] == "application/pdf"
    assert bt.verify_set(set_dir) == []


def test_s3_backup_fails_on_etag_mismatch(tmp_path, s3_env):
    key = "evidence/alc-1/act-1/a.pdf"
    source = FakeS3(EVIDENCE, etag_override={key: '"' + "0" * 32 + '"'})
    set_dir, _ = bt.begin_set(tmp_path, "alc_growth")
    with pytest.raises(bt.BackupError, match="ETag differs"):
        bt.storage_backup(set_dir, "s3", client=source)


def test_s3_backup_refuses_unsafe_keys(tmp_path, s3_env):
    set_dir, _ = bt.begin_set(tmp_path, "alc_growth")
    with pytest.raises(bt.BackupError, match="unsupported object key"):
        bt.storage_backup(set_dir, "s3", client=FakeS3({"../escape.pdf": b"x"}))
    assert not (tmp_path / "escape.pdf").exists()


def test_s3_restore_uploads_exact_keys_and_never_deletes(tmp_path, s3_env, fake_dump):
    set_dir = s3_set(tmp_path, FakeS3(EVIDENCE), fake_dump)
    target = FakeS3({"unrelated/keep.txt": b"keep"})
    result = bt.storage_restore(set_dir, "s3", client=target, deep=True)
    assert result["upload"] == 3 and result["untouched_other"] == 1 and result["verified"] == 3
    restored = {k: v[0] for k, v in target.objects.items()}
    assert restored == {**EVIDENCE, "unrelated/keep.txt": b"keep"}
    again = bt.storage_restore(set_dir, "s3", client=target)  # idempotent
    assert again["identical"] == 3 and len(target.puts) == 3


def test_s3_restore_conflict_requires_force_and_confirmation(
    tmp_path, s3_env, fake_dump, monkeypatch
):
    set_dir = s3_set(tmp_path, FakeS3(EVIDENCE), fake_dump)
    key = "evidence/alc-1/act-1/a.pdf"
    target = FakeS3({key: b"different content"})
    with pytest.raises(bt.BackupError, match="nothing was written"):
        bt.storage_restore(set_dir, "s3", client=target)
    assert target.puts == []
    with pytest.raises(bt.BackupError, match="RESTORE_CONFIRM"):
        bt.storage_restore(set_dir, "s3", client=target, force=True)
    monkeypatch.setenv("RESTORE_CONFIRM", "YES")
    with pytest.raises(bt.BackupError, match="RESTORE_CONFIRM"):
        bt.storage_restore(set_dir, "s3", client=target, force=True)
    assert target.puts == []
    monkeypatch.setenv("RESTORE_CONFIRM", "alc-evidence")
    bt.storage_restore(set_dir, "s3", client=target, force=True)
    assert target.objects[key][0] == EVIDENCE[key]


def test_local_restore_round_trip(tmp_path, complete_set, monkeypatch):
    target = tmp_path / "restored-store"
    (target / "other").mkdir(parents=True)
    (target / "other/keep.txt").write_bytes(b"keep")
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(target))
    result = bt.storage_restore(complete_set, "local")
    assert result["upload"] == 3 and result["untouched_other"] == 1
    for key, body in EVIDENCE.items():
        assert target.joinpath(*key.split("/")).read_bytes() == body
    assert (target / "other/keep.txt").read_bytes() == b"keep"


def test_restore_refuses_an_incomplete_set(complete_set):
    (complete_set / "INCOMPLETE").write_text("x")
    with pytest.raises(bt.BackupError, match="not complete"):
        bt.storage_restore(complete_set, "local")


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #
def fake_set(root: Path, set_id: str, *, valid: bool = True) -> Path:
    """A structurally valid set (sizes only; retention does not re-hash)."""
    set_dir = root / set_id
    (set_dir / "database").mkdir(parents=True)
    (set_dir / "storage" / "objects").mkdir(parents=True)
    dump = set_dir / "database" / f"alc_growth_db_{set_id}.dump"
    dump.write_bytes(b"dump")
    (set_dir / "storage" / "inventory.json").write_text(json.dumps({"objects": []}))
    manifest = {"backup_format_version": 1, "set_id": set_id, "status": "complete",
                "database": {"dump_file": f"database/{dump.name}", "dump_size": 4},
                "storage": {"object_count": 0}}
    (set_dir / "manifest.json").write_text(json.dumps(manifest))
    if not valid:
        (set_dir / "INCOMPLETE").write_text("failed_step=storage backup\n")
    return set_dir


def names(paths):
    return [p.name for p in paths]


def test_retention_keeps_daily_weekly_monthly(tmp_path):
    ids = [f"202610{day:02d}T020000Z" for day in range(1, 31)] + \
          ["20260915T020000Z", "20260815T020000Z", "20260715T020000Z", "20260615T020000Z"]
    for set_id in ids:
        fake_set(tmp_path, set_id)
    plan = bt.retention_plan(tmp_path, daily=7, weekly=4, monthly=3)
    kept = [p.name for p, _ in plan["keep"]]
    assert kept[:7] == [f"202610{day:02d}T020000Z" for day in range(30, 23, -1)]
    # The newest set of each earlier ISO week (weeks start on Monday 19, 12 and 5 October).
    assert {"20261018T020000Z", "20261011T020000Z"} <= set(kept)
    assert "20261019T020000Z" not in kept
    assert "20260915T020000Z" in kept and "20260815T020000Z" in kept  # monthly
    assert "20260715T020000Z" not in kept and "20260615T020000Z" not in kept
    assert set(names(plan["delete"])) == set(ids) - set(kept)


def test_retention_always_keeps_the_newest_valid_set(tmp_path):
    fake_set(tmp_path, "20250101T000000Z")
    plan = bt.retention_plan(tmp_path, daily=1, weekly=0, monthly=0)
    assert names(p for p, _ in plan["keep"]) == ["20250101T000000Z"] and plan["delete"] == []


def test_retention_never_deletes_when_no_valid_set_exists(tmp_path):
    for set_id in ("20261001T000000Z", "20261002T000000Z"):
        fake_set(tmp_path, set_id, valid=False)
    plan = bt.prune(tmp_path, apply=True, daily=1, weekly=0, monthly=0)
    assert plan["delete"] == [] and len(bt.set_dirs(tmp_path)) == 2


def test_retention_handles_incomplete_sets(tmp_path):
    fake_set(tmp_path, "20261001T000000Z", valid=False)  # superseded failure
    fake_set(tmp_path, "20261002T000000Z")
    fake_set(tmp_path, "20261003T000000Z", valid=False)  # newer than the newest valid set
    plan = bt.retention_plan(tmp_path, daily=7, weekly=4, monthly=3)
    assert names(plan["delete"]) == ["20261001T000000Z"]
    assert names(plan["kept_incomplete"]) == ["20261003T000000Z"]


@POSIX_ONLY
def test_retention_ignores_anything_that_is_not_a_set(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep")
    root = tmp_path / "backups"
    root.mkdir()
    fake_set(root, "20261001T000000Z")
    fake_set(root, "20261002T000000Z")
    (root / "20261003T000000Z").symlink_to(outside)  # symlinked "set" pointing outside
    (root / "2026-10-04").mkdir()  # malformed name
    (root / "notes.txt").write_text("keep")
    (root / "20260101T000000Z.tar").write_text("keep")
    bt.prune(root, apply=True, daily=1, weekly=0, monthly=0)
    assert sorted(p.name for p in root.iterdir()) == sorted([
        "20260101T000000Z.tar", "20261002T000000Z", "20261003T000000Z", "2026-10-04", "notes.txt"])
    assert (outside / "precious.txt").read_text() == "keep"


def test_retention_dry_run_deletes_nothing(tmp_path):
    for set_id in ("20261001T000000Z", "20261002T000000Z"):
        fake_set(tmp_path, set_id)
    plan = bt.prune(tmp_path, apply=False, daily=1, weekly=0, monthly=0)
    assert names(plan["delete"]) == ["20261001T000000Z"] and len(bt.set_dirs(tmp_path)) == 2


def test_remove_set_refuses_paths_outside_the_root(tmp_path):
    root = tmp_path / "backups"
    root.mkdir()
    elsewhere = fake_set(tmp_path, "20261001T000000Z")
    with pytest.raises(bt.BackupError, match="refusing to delete"):
        bt.remove_set(root, elsewhere)
    with pytest.raises(bt.BackupError, match="refusing to delete"):
        bt.remove_set(root, root / "notes")
    assert elsewhere.exists()


def test_prune_command_validates_the_root():
    assert bt.main(["prune", "/", "--apply"]) == 1
    assert bt.main(["prune", "", "--apply"]) == 1


def test_secrets_are_not_logged(tmp_path, fake_dump, local_store, monkeypatch, capsys):
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    root = tmp_path / "backups"
    root.mkdir()
    make_complete_set(root)
    bt.main(["prune", str(root), "--apply"])
    err = capsys.readouterr().err
    assert all(value not in err for value in SECRETS.values())
    assert os.environ["S3_SECRET_KEY"] == SECRETS["S3_SECRET_KEY"]
