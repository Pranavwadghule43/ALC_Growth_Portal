#!/usr/bin/env python3
"""Backup helper for the ALC Growth Portal backup / restore scripts.

The shell scripts in this directory are the operator entry points (``backup.sh``,
``verify_backup.sh``, ``restore_postgres.sh``, ``restore_storage.sh``, ``retention.sh``); they call
this helper for everything that is easier to get right in Python:

* ``check-root``      validate (and optionally create) BACKUP_ROOT — the single implementation
* ``begin-set``       create a new, INCOMPLETE backup set directory and choose the dump filename
* ``storage-backup``  copy every evidence object (S3/MinIO or local storage), keys unchanged
* ``finalize``        read the dump, verify the whole set, then write manifest.json (complete)
* ``verify``          verify an existing set without restoring anything
* ``describe``        print the dump path and Alembic revision of a verified set
* ``storage-restore`` restore evidence objects exactly, never deleting anything
* ``prune``           retention (daily / weekly / monthly), confined to BACKUP_ROOT

Configuration comes from the environment (the same S3_* / STORAGE_BACKEND / LOCAL_STORAGE_PATH
variables as the API). Credentials are only ever passed to the S3 client: they are never
logged, printed or written to a manifest. Standard library only, plus boto3 for S3.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit

FORMAT_VERSION = 1
SET_ID_RE = re.compile(r"^\d{8}T\d{6}Z$")
SET_ID_FORMAT = "%Y%m%dT%H%M%SZ"
MANIFEST = "manifest.json"
INCOMPLETE = "INCOMPLETE"
DATABASE_DIR = "database"
STORAGE_DIR = "storage"
OBJECTS_DIR = "storage/objects"
INVENTORY = "storage/inventory.json"
CHUNK = 1024 * 1024
MD5_ETAG_RE = re.compile(r"^[0-9a-f]{32}$")
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
CONSISTENCY_NOTE = (
    "The database dump and the storage copy are taken one after the other, not atomically. "
    "They form a consistent pair only if application writes were frozen for the whole run "
    "(see docs/BACKUP_AND_RECOVERY.md)."
)
DEFAULT_RETENTION = {"daily": 7, "weekly": 4, "monthly": 3}

# Top-level / system locations that must never be a backup root (retention deletes inside it).
POSIX_FORBIDDEN = {
    "/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib32", "/lib64", "/libx32",
    "/media", "/mnt", "/opt", "/proc", "/root", "/run", "/sbin", "/srv", "/sys", "/tmp", "/usr",
    "/var", "/var/backups", "/var/cache", "/var/lib", "/var/log", "/var/tmp", "/Users",
    "/Volumes", "/private", "/System", "/Library", "/Applications",
}
WINDOWS_FORBIDDEN = {
    "windows", "users", "program files", "program files (x86)", "programdata", "temp",
}


class BackupError(Exception):
    """An operational failure: reported on one line, exit status 1."""


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(message: str) -> None:
    print(f"{iso(utcnow())} [backup] {message}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# Backup root
# --------------------------------------------------------------------------- #
def repository_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    return here.parents[4]  # apps/api/scripts/backup/backup_tool.py -> repository


def root_problem(raw: str, *, windows: bool | None = None) -> str | None:
    """Why ``raw`` must not be used as BACKUP_ROOT, or None when it is acceptable.

    The root must be a dedicated absolute directory: never empty, relative, a filesystem or
    drive root, a system or home directory, the repository (or one of its parents), and never
    reached through ``..``."""
    value = (raw or "").strip()
    if not value:
        return "BACKUP_ROOT is empty"
    if value != raw:
        return "BACKUP_ROOT must not have leading or trailing whitespace"
    if "\x00" in value or "\n" in value:
        return "BACKUP_ROOT contains control characters"
    if windows is None:
        windows = (os.name == "nt" or bool(re.match(r"^[A-Za-z]:", value))
                   or value.startswith("\\\\"))
    flavour: type[PurePath] = PureWindowsPath if windows else PurePosixPath
    path = flavour(value)
    if not path.is_absolute():
        return "BACKUP_ROOT must be an absolute path"
    if any(part in (".", "..") for part in re.split(r"[\\/]+", value)):
        return "BACKUP_ROOT must not contain '.' or '..' components"
    parts = path.parts
    if windows:
        if len(parts) <= 1:
            return "BACKUP_ROOT must not be a drive or share root"
        if len(parts) == 2 and parts[1].lower() in WINDOWS_FORBIDDEN:
            return f"BACKUP_ROOT must not be the system directory {path}"
        if len(parts) == 3 and parts[1].lower() == "users":
            return "BACKUP_ROOT must not be a user's home directory"
    else:
        normal = str(path)
        if normal in POSIX_FORBIDDEN:
            return f"BACKUP_ROOT must not be the system directory {normal}"
        if len(parts) == 3 and parts[1] in ("home", "Users"):
            return "BACKUP_ROOT must not be a user's home directory"
    if not windows or os.name == "nt":
        actual = Path(value)
        repo = repository_root()
        if actual == repo or actual in repo.parents:
            return "BACKUP_ROOT must not be the repository or one of its parent directories"
    return None


def validate_root(raw: str, *, create: bool = False) -> Path:
    problem = root_problem(raw)
    if problem:
        raise BackupError(problem)
    root = Path(raw)
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise BackupError("BACKUP_ROOT must be a real directory (not a file or symlink)")
    resolved = root.resolve()
    problem = root_problem(str(resolved))
    if problem:
        raise BackupError(f"BACKUP_ROOT resolves to an unsafe location: {problem}")
    if not root.exists():
        if not create:
            raise BackupError(f"BACKUP_ROOT does not exist: {root}")
        root.mkdir(mode=0o700, parents=True)
        log(f"created backup root {root}")
    return root


def set_dirs(root: Path) -> list[Path]:
    """Backup set directories directly inside ``root`` (strict names, no symlinks)."""
    return sorted(
        child for child in root.iterdir()
        if SET_ID_RE.match(child.name) and child.is_dir() and not child.is_symlink()
    )


def set_time(set_id: str) -> dt.datetime:
    return dt.datetime.strptime(set_id, SET_ID_FORMAT).replace(tzinfo=dt.timezone.utc)


def dump_filename(database: str, set_id: str) -> str:
    """``<database>_db_<set id>.dump`` with every unsafe character replaced."""
    if not SET_ID_RE.match(set_id):
        raise BackupError(f"invalid backup set id {set_id!r}")
    name = SAFE_NAME_RE.sub("_", database).strip("_") or "database"
    return f"{name[:60]}_db_{set_id}.dump"


# --------------------------------------------------------------------------- #
# Files, keys and checksums
# --------------------------------------------------------------------------- #
def key_problem(key: str) -> str | None:
    """Object keys become relative file paths, so only plain relative keys are accepted."""
    if not key:
        return "empty key"
    if key.startswith("/") or key.endswith("/"):
        return "key starts or ends with '/'"
    if any(ord(ch) < 32 or ch in '\\<>:"|?*' for ch in key):
        return "key contains a character that is unsafe in file names"
    if any(part in ("", ".", "..") for part in key.split("/")):
        return "key contains an empty, '.' or '..' segment"
    return None


def object_path(objects_dir: Path, key: str) -> Path:
    problem = key_problem(key)
    if problem:
        raise BackupError(f"unsupported object key {key!r}: {problem}")
    return objects_dir.joinpath(*key.split("/"))


class Digest:
    def __init__(self) -> None:
        self.sha256 = hashlib.sha256()
        self.md5 = hashlib.md5(usedforsecurity=False)
        self.size = 0

    def update(self, chunk: bytes) -> None:
        self.sha256.update(chunk)
        self.md5.update(chunk)
        self.size += len(chunk)


def file_digest(path: Path) -> Digest:
    digest = Digest()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest


def write_stream(chunks: Iterable[bytes], dest: Path) -> Digest:
    """Write ``chunks`` to ``dest`` via a temporary file, returning the content digest."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    digest = Digest()
    try:
        with tmp.open("wb") as handle:
            for chunk in chunks:
                digest.update(chunk)
                handle.write(chunk)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return digest


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError(f"cannot read {path.name}: {type(exc).__name__}") from None
    if not isinstance(data, dict):
        raise BackupError(f"{path.name} is not a JSON object")
    return data


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise BackupError(f"environment variable {name} must be set")
    return value


# --------------------------------------------------------------------------- #
# Storage backends
# --------------------------------------------------------------------------- #
def s3_client():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT_URL", "").strip() or None,
        region_name=os.environ.get("S3_REGION", "").strip() or "us-east-1",
        aws_access_key_id=require_env("S3_ACCESS_KEY"),
        aws_secret_access_key=require_env("S3_SECRET_KEY"),
        config=Config(retries={"max_attempts": 5, "mode": "standard"},
                      connect_timeout=10, read_timeout=120),
    )


def s3_target() -> str:
    """Non-secret description of the configured bucket (endpoint host, never credentials)."""
    endpoint = os.environ.get("S3_ENDPOINT_URL", "").strip()
    host = urlsplit(endpoint).hostname or "AWS S3" if endpoint else "AWS S3"
    port = urlsplit(endpoint).port if endpoint else None
    return f"bucket {require_env('S3_BUCKET')} at {host}{f':{port}' if port else ''}"


def etag_md5(etag: str | None, encrypted: bool) -> str | None:
    """The object's MD5 when its ETag is one (single-part upload, no server-side encryption)."""
    value = (etag or "").strip('"').lower()
    return value if MD5_ETAG_RE.match(value) and not encrypted else None


def s3_listing(client, bucket: str) -> Iterator[dict]:
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        yield from page.get("Contents", [])


def local_root() -> Path:
    root = Path(require_env("LOCAL_STORAGE_PATH")).resolve()
    if not root.is_dir():
        raise BackupError(f"LOCAL_STORAGE_PATH is not a directory: {root}")
    return root


def local_listing(root: Path) -> Iterator[tuple[str, Path]]:
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            relative = path.relative_to(root)
            raise BackupError(f"symlink in local storage is not supported: {relative}")
        if path.is_file():
            yield path.relative_to(root).as_posix(), path


def file_chunks(path: Path) -> Iterator[bytes]:
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            yield chunk


def body_chunks(body) -> Iterator[bytes]:
    try:
        while chunk := body.read(CHUNK):
            yield chunk
    finally:
        body.close()


def storage_backup(set_dir: Path, backend: str, *, client=None) -> dict:
    """Copy every object, keys unchanged, into ``storage/objects`` and write the inventory."""
    objects_dir = set_dir / OBJECTS_DIR
    objects_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    md5_checked = 0
    if backend == "s3":
        bucket = require_env("S3_BUCKET")
        client = client or s3_client()
        log(f"storage backup started ({s3_target()})")
        skip_etag = os.environ.get("BACKUP_SKIP_ETAG_CHECK") == "1"
        for item in s3_listing(client, bucket):
            key = item["Key"]
            dest = object_path(objects_dir, key)
            response = client.get_object(Bucket=bucket, Key=key)
            digest = write_stream(body_chunks(response["Body"]), dest)
            expected_size = response.get("ContentLength", item.get("Size"))
            if expected_size is not None and digest.size != expected_size:
                raise BackupError(f"size mismatch while copying {key}")
            server_md5 = etag_md5(response.get("ETag"), bool(response.get("ServerSideEncryption")))
            if server_md5 and not skip_etag:
                if server_md5 != digest.md5.hexdigest():
                    raise BackupError(f"checksum mismatch while copying {key} (ETag differs)")
                md5_checked += 1
            entries.append(inventory_entry(key, digest, response.get("ContentType"),
                                           response.get("ETag")))
        source = {"backend": "s3", "bucket": bucket}
    elif backend == "local":
        root = local_root()
        log(f"storage backup started (local storage {root})")
        for key, path in local_listing(root):
            digest = write_stream(file_chunks(path), object_path(objects_dir, key))
            entries.append(inventory_entry(key, digest, None, None))
        source = {"backend": "local"}
    else:
        raise BackupError("STORAGE_BACKEND must be s3 or local")
    entries.sort(key=lambda entry: entry["key"])
    inventory = {"format": FORMAT_VERSION, **source, "objects": entries,
                 "object_count": len(entries), "total_bytes": sum(e["size"] for e in entries),
                 "etag_md5_checked": md5_checked}
    write_json(set_dir / INVENTORY, inventory)
    log(f"storage backup completed: {len(entries)} objects, {inventory['total_bytes']} bytes"
        f" ({md5_checked} also checked against the server ETag)")
    return inventory


def inventory_entry(key: str, digest: Digest, content_type: str | None, etag: str | None) -> dict:
    entry = {"key": key, "size": digest.size, "sha256": digest.sha256.hexdigest(),
             "md5": digest.md5.hexdigest()}
    if content_type:
        entry["content_type"] = content_type
    if etag:
        entry["etag"] = etag.strip('"')
    return entry


# --------------------------------------------------------------------------- #
# Database dump inspection (reads the dump itself, never the live database)
# --------------------------------------------------------------------------- #
COPY_ESCAPES = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\"}


def copy_value(raw: str) -> str | None:
    if raw == "\\N":
        return None
    return re.sub(r"\\(.)", lambda m: COPY_ESCAPES.get(m.group(1), m.group(1)), raw)


def pg_restore_bin() -> str:
    return os.environ.get("PG_RESTORE", "pg_restore")


def dump_table(dump: Path, table: str) -> tuple[list[str], list[list[str | None]]] | None:
    """Columns and rows of ``table`` in a custom-format dump, or None if it has no data."""
    result = subprocess.run(
        [pg_restore_bin(), "--data-only", f"--table={table}", "--file=-", str(dump)],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        raise BackupError(f"pg_restore could not read table {table} from the dump")
    lines = iter(result.stdout.splitlines())
    for line in lines:
        match = re.match(r"^COPY \S+ \((.*)\) FROM stdin;$", line)
        if match:
            columns = [c.strip().strip('"') for c in match.group(1).split(",")]
            rows = []
            for row in lines:
                if row == "\\.":
                    return columns, rows
                rows.append([copy_value(value) for value in row.split("\t")])
            raise BackupError(f"unterminated COPY data for {table} in the dump")
    return None


def dump_alembic_revision(dump: Path) -> str | None:
    table = dump_table(dump, "alembic_version")
    if not table or not table[1]:
        return None
    columns, rows = table
    index = columns.index("version_num")
    return ",".join(sorted(str(row[index]) for row in rows))


def dump_evidence_keys(dump: Path) -> tuple[int, list[str]]:
    """(evidence rows, storage keys of active evidence) as recorded in the dump."""
    table = dump_table(dump, "activity_evidence")
    if not table:
        return 0, []
    columns, rows = table
    key_at, active_at = columns.index("storage_key"), columns.index("is_active")
    return len(rows), sorted(str(r[key_at]) for r in rows if r[active_at] == "t")


def pg_restore_lists(dump: Path) -> bool:
    result = subprocess.run([pg_restore_bin(), "--list", str(dump)], capture_output=True)
    return result.returncode == 0


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #
def inside(set_dir: Path, relative: str) -> Path | None:
    """``relative`` resolved inside ``set_dir``, or None if it would escape it."""
    if not relative or PurePosixPath(relative).is_absolute() or ".." in relative.split("/"):
        return None
    return set_dir.joinpath(*relative.split("/"))


def verify_set(set_dir: Path, *, deep: bool = True, check_dump: bool = True,
               finalizing: bool = False) -> list[str]:
    """Problems with a backup set (empty when it is a valid, complete set).

    ``deep`` re-hashes the dump and every stored object; otherwise only presence and sizes are
    checked (used by retention). ``check_dump`` also asks pg_restore to read the dump.
    ``finalizing`` is the check ``finalize`` runs while the INCOMPLETE marker is still present."""
    problems: list[str] = []
    if not SET_ID_RE.match(set_dir.name):
        problems.append(f"directory name {set_dir.name!r} is not a backup set id")
    if not set_dir.is_dir():
        return [*problems, "backup set directory does not exist"]
    if (set_dir / INCOMPLETE).exists() and not finalizing:
        problems.append("set is marked INCOMPLETE")
    if not (set_dir / MANIFEST).is_file():
        return [*problems, "manifest.json is missing"]
    try:
        manifest = read_json(set_dir / MANIFEST)
    except BackupError as exc:
        return [*problems, str(exc)]
    if manifest.get("backup_format_version") != FORMAT_VERSION:
        problems.append("unsupported backup_format_version")
    if manifest.get("status") != "complete":
        problems.append(f"manifest status is {manifest.get('status')!r}, not 'complete'")
    if manifest.get("set_id") != set_dir.name:
        problems.append("manifest set_id does not match the directory name")

    database = manifest.get("database") or {}
    dump = inside(set_dir, str(database.get("dump_file", "")))
    if dump is None or not dump.is_file():
        problems.append("database dump file is missing")
    elif dump.stat().st_size != database.get("dump_size"):
        problems.append("database dump size does not match the manifest")
    elif deep and file_digest(dump).sha256.hexdigest() != database.get("dump_sha256"):
        problems.append("database dump SHA256 does not match the manifest")
    elif check_dump and not pg_restore_lists(dump):
        problems.append("pg_restore cannot read the database dump")

    storage = manifest.get("storage") or {}
    inventory_path = set_dir / INVENTORY
    if not inventory_path.is_file():
        return [*problems, "storage inventory is missing"]
    if deep and file_digest(inventory_path).sha256.hexdigest() != storage.get("inventory_sha256"):
        return [*problems, "storage inventory SHA256 does not match the manifest"]
    try:
        inventory = read_json(inventory_path)
    except BackupError as exc:
        return [*problems, str(exc)]
    objects = inventory.get("objects") or []
    if len(objects) != storage.get("object_count"):
        problems.append("storage object count does not match the manifest")
    objects_dir = set_dir / OBJECTS_DIR
    expected: set[Path] = set()
    for entry in objects:
        key = str(entry.get("key", ""))
        if key_problem(key):
            problems.append(f"inventory has an unsafe key {key!r}")
            continue
        path = object_path(objects_dir, key)
        expected.add(path)
        if not path.is_file():
            problems.append(f"stored object missing: {key}")
        elif path.stat().st_size != entry.get("size"):
            problems.append(f"stored object size differs: {key}")
        elif deep and file_digest(path).sha256.hexdigest() != entry.get("sha256"):
            problems.append(f"stored object SHA256 differs: {key}")
    if objects_dir.is_dir():
        extra = [p for p in objects_dir.rglob("*") if p.is_file() and p not in expected]
        if extra:
            problems.append(f"{len(extra)} unexpected file(s) in storage/objects")
    elif objects:
        problems.append("storage/objects directory is missing")
    return problems


# --------------------------------------------------------------------------- #
# Finalize (manifest)
# --------------------------------------------------------------------------- #
def tool_versions() -> dict:
    versions = {"python": platform.python_version()}
    try:
        import boto3

        versions["boto3"] = boto3.__version__
    except ImportError:
        pass
    return versions


def finalize(set_dir: Path, *, dump: Path, database_name: str, server_version: str | None,
             pg_dump_version: str | None, app_commit: str | None) -> dict:
    """Verify everything, then write the complete manifest and remove the INCOMPLETE marker."""
    if dump.parent != set_dir / DATABASE_DIR or not dump.is_file():
        raise BackupError("dump file must be inside the set's database directory")
    inventory_path = set_dir / INVENTORY
    inventory = read_json(inventory_path)
    if not pg_restore_lists(dump):
        raise BackupError("pg_restore cannot read the database dump")
    revision = dump_alembic_revision(dump)
    evidence_rows, active_keys = dump_evidence_keys(dump)
    stored = {entry["key"] for entry in inventory["objects"]}
    missing = [key for key in active_keys if key not in stored]
    warnings = []
    if revision is None:
        warnings.append("the dump has no alembic_version row")
    if missing:
        warnings.append(f"{len(missing)} active evidence object(s) referenced by the dump are "
                        "not in the storage copy")
        log(f"WARNING: {len(missing)} active evidence object(s) referenced by the database are "
            f"missing from storage, e.g. {missing[:3]}")
    started = set_time(set_dir.name)
    dump_digest = file_digest(dump)
    manifest = {
        "backup_format_version": FORMAT_VERSION,
        "set_id": set_dir.name,
        "status": "complete",
        "started_at": iso(started),
        "completed_at": iso(utcnow()),
        "host": socket.gethostname(),
        "application": {"git_commit": app_commit or None},
        "database": {
            "engine": "postgresql",
            "name": database_name,
            "dump_file": dump.relative_to(set_dir).as_posix(),
            "dump_format": "custom",
            "dump_size": dump_digest.size,
            "dump_sha256": dump_digest.sha256.hexdigest(),
            "alembic_revision": revision,
            "server_version": server_version or None,
            "pg_dump_version": pg_dump_version or None,
        },
        "storage": {
            "backend": inventory.get("backend"),
            "bucket": inventory.get("bucket"),
            "objects_dir": OBJECTS_DIR,
            "inventory_file": INVENTORY,
            "inventory_sha256": file_digest(inventory_path).sha256.hexdigest(),
            "object_count": len(inventory["objects"]),
            "total_bytes": inventory.get("total_bytes"),
            "etag_md5_checked": inventory.get("etag_md5_checked", 0),
            "verification": "every object re-read and matched against its SHA256 in the inventory",
        },
        "references": {
            "evidence_rows": evidence_rows,
            "active_evidence_rows": len(active_keys),
            "active_evidence_missing_objects": len(missing),
        },
        "tools": tool_versions(),
        "consistency": CONSISTENCY_NOTE,
        "warnings": warnings,
    }
    manifest["storage"] = {k: v for k, v in manifest["storage"].items() if v is not None}
    write_json(set_dir / MANIFEST, manifest)
    problems = verify_set(set_dir, finalizing=True)
    if problems:
        (set_dir / MANIFEST).unlink()
        raise BackupError("verification failed: " + "; ".join(problems))
    (set_dir / INCOMPLETE).unlink(missing_ok=True)
    log(f"verification completed; manifest written ({set_dir / MANIFEST})")
    return manifest


# --------------------------------------------------------------------------- #
# Storage restore
# --------------------------------------------------------------------------- #
def load_verified_inventory(set_dir: Path) -> list[dict]:
    manifest = read_json(set_dir / MANIFEST)
    if manifest.get("status") != "complete" or (set_dir / INCOMPLETE).exists():
        raise BackupError("the backup set is not complete")
    inventory_path = set_dir / INVENTORY
    if file_digest(inventory_path).sha256.hexdigest() != manifest["storage"]["inventory_sha256"]:
        raise BackupError("storage inventory does not match the manifest")
    return read_json(inventory_path)["objects"]


def confirm_overwrite(target: str) -> None:
    if os.environ.get("RESTORE_CONFIRM", "") != target:
        raise BackupError(
            f"refusing to overwrite objects in {target!r}: set RESTORE_CONFIRM={target} "
            "together with --force to confirm"
        )


def storage_restore(set_dir: Path, backend: str, *, force: bool = False, deep: bool = False,
                    client=None) -> dict:
    """Restore every inventoried object under its exact key. Objects already identical are
    skipped; differing ones are only overwritten with --force (and RESTORE_CONFIRM); objects
    that are not in the backup are never touched or deleted."""
    objects = load_verified_inventory(set_dir)
    objects_dir = set_dir / OBJECTS_DIR
    if backend == "s3":
        bucket = require_env("S3_BUCKET")
        client = client or s3_client()
        log(f"storage restore target: {s3_target()}")
        existing = {item["Key"]: item for item in s3_listing(client, bucket)}

        def same(entry, current):
            if current["Size"] != entry["size"]:
                return False
            current_md5 = etag_md5(current.get("ETag"), False)
            return None if current_md5 is None else current_md5 == entry["md5"]
        target_name = bucket
    elif backend == "local":
        root = Path(require_env("LOCAL_STORAGE_PATH")).resolve()
        root.mkdir(parents=True, exist_ok=True)
        log(f"storage restore target: local storage {root}")
        existing = {key: {"path": path} for key, path in local_listing(root)}

        def same(entry, current):
            return file_digest(current["path"]).sha256.hexdigest() == entry["sha256"]
        target_name = str(root)
    else:
        raise BackupError("STORAGE_BACKEND must be s3 or local")

    plan = {"upload": [], "identical": [], "conflict": []}
    for entry in objects:
        current = existing.get(entry["key"])
        if current is None:
            plan["upload"].append(entry)
        elif same(entry, current):
            plan["identical"].append(entry)
        else:
            plan["conflict"].append(entry)  # differs, or cannot be proven identical
    extra = len(set(existing) - {entry["key"] for entry in objects})
    log(f"storage restore plan: {len(plan['upload'])} to upload, {len(plan['identical'])} "
        f"already identical, {len(plan['conflict'])} existing and different, {extra} other "
        "object(s) in the target left untouched")
    if plan["conflict"]:
        if not force:
            raise BackupError(
                f"{len(plan['conflict'])} object(s) already exist with different content, e.g. "
                f"{plan['conflict'][0]['key']}; nothing was written. Re-run with --force and "
                f"RESTORE_CONFIRM={target_name} to overwrite them"
            )
        confirm_overwrite(target_name)
    for entry in plan["upload"] + plan["conflict"]:
        source = object_path(objects_dir, entry["key"])
        if backend == "s3":
            extra_args = {"ContentType": entry["content_type"]} if entry.get("content_type") else {}
            with source.open("rb") as body:
                client.put_object(Bucket=bucket, Key=entry["key"], Body=body,
                                  ContentLength=entry["size"], **extra_args)
        else:
            digest = write_stream(file_chunks(source), object_path(root, entry["key"]))
            if digest.sha256.hexdigest() != entry["sha256"]:
                raise BackupError(f"restored file differs from the backup: {entry['key']}")
    verified = restore_check(objects, backend, client=client, deep=deep,
                             bucket=bucket if backend == "s3" else None,
                             root=root if backend == "local" else None)
    log(f"storage restore completed: {len(plan['upload']) + len(plan['conflict'])} written, "
        f"{verified} verified")
    return {k: len(v) for k, v in plan.items()} | {"untouched_other": extra, "verified": verified}


def restore_check(objects: list[dict], backend: str, *, client, deep: bool, bucket: str | None,
                  root: Path | None) -> int:
    for entry in objects:
        key = entry["key"]
        if backend == "local":
            assert root is not None
            if file_digest(object_path(root, key)).sha256.hexdigest() != entry["sha256"]:
                raise BackupError(f"restored object does not match the backup: {key}")
            continue
        head = client.head_object(Bucket=bucket, Key=key)
        if head["ContentLength"] != entry["size"]:
            raise BackupError(f"restored object has the wrong size: {key}")
        server_md5 = etag_md5(head.get("ETag"), bool(head.get("ServerSideEncryption")))
        if server_md5 and server_md5 != entry["md5"]:
            raise BackupError(f"restored object ETag does not match the backup: {key}")
        if deep:
            body = client.get_object(Bucket=bucket, Key=key)["Body"]
            digest = Digest()
            for chunk in body_chunks(body):
                digest.update(chunk)
            if digest.sha256.hexdigest() != entry["sha256"]:
                raise BackupError(f"restored object content does not match the backup: {key}")
    return len(objects)


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #
def retention_plan(root: Path, *, daily: int, weekly: int, monthly: int) -> dict[str, list]:
    """Which sets to keep and delete. Only valid (complete) sets count as backups; the newest
    valid set is always kept; incomplete sets are deleted only when a newer valid set exists;
    anything that is not a backup set directory is ignored."""
    valid, incomplete = [], []
    for path in set_dirs(root):
        (incomplete if verify_set(path, deep=False, check_dump=False) else valid).append(path)
    valid.sort(key=lambda p: p.name, reverse=True)
    keep: dict[Path, list[str]] = {}
    if valid:
        keep[valid[0]] = ["newest valid backup"]
    for label, count, bucket in (
        ("daily", daily, lambda t: t.date()),
        ("weekly", weekly, lambda t: t.isocalendar()[:2]),
        ("monthly", monthly, lambda t: (t.year, t.month)),
    ):
        seen = []
        for path in valid:
            period = bucket(set_time(path.name))
            if period not in seen and len(seen) < count:
                seen.append(period)
                keep.setdefault(path, []).append(label)
    delete = [p for p in valid if p not in keep]
    newest_valid = valid[0].name if valid else None
    delete += [p for p in incomplete if newest_valid and p.name < newest_valid]
    kept_incomplete = [p for p in incomplete if p not in delete]
    return {"keep": [(p, keep[p]) for p in sorted(keep, key=lambda p: p.name, reverse=True)],
            "delete": sorted(delete, key=lambda p: p.name),
            "kept_incomplete": kept_incomplete}


def prune(root: Path, *, apply: bool, daily: int, weekly: int, monthly: int) -> dict:
    if min(daily, weekly, monthly) < 0 or daily < 1:
        raise BackupError("retention counts must be >= 0, and daily >= 1")
    plan = retention_plan(root, daily=daily, weekly=weekly, monthly=monthly)
    for path, reasons in plan["keep"]:
        log(f"keep    {path.name} ({', '.join(reasons)})")
    for path in plan["kept_incomplete"]:
        log(f"keep    {path.name} (incomplete; newer than the newest valid backup)")
    for path in plan["delete"]:
        log(f"{'delete ' if apply else 'would delete'} {path.name}")
        if apply:
            remove_set(root, path)
    if not plan["keep"]:
        log("no valid backup set exists: nothing deleted")
    return plan


def remove_set(root: Path, path: Path) -> None:
    """Delete one backup set, re-checking that it is a set directory directly inside root."""
    if (path.parent.resolve() != root.resolve() or not SET_ID_RE.match(path.name)
            or path.is_symlink() or not path.is_dir()):
        raise BackupError(f"refusing to delete {path}: not a backup set inside {root}")
    shutil.rmtree(path)


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #
def begin_set(root: Path, database: str) -> tuple[Path, Path]:
    set_id = utcnow().strftime(SET_ID_FORMAT)
    set_dir = root / set_id
    set_dir.mkdir(mode=0o700)  # fails if a set with this id already exists
    (set_dir / INCOMPLETE).write_text(f"started_at={iso(utcnow())}\n", encoding="utf-8")
    (set_dir / DATABASE_DIR).mkdir()
    (set_dir / STORAGE_DIR).mkdir()
    return set_dir, set_dir / DATABASE_DIR / dump_filename(database, set_id)


def describe(set_dir: Path) -> dict:
    problems = verify_set(set_dir)
    if problems:
        raise BackupError("backup set failed verification: " + "; ".join(problems))
    manifest = read_json(set_dir / MANIFEST)
    database = manifest["database"]
    return {"dump": str(set_dir.joinpath(*database["dump_file"].split("/")).resolve()),
            "alembic_revision": database.get("alembic_revision") or ""}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check-root")
    p.add_argument("root")
    p.add_argument("--create", action="store_true")
    p = sub.add_parser("begin-set")
    p.add_argument("root")
    p.add_argument("--database", required=True)
    p = sub.add_parser("storage-backup")
    p.add_argument("set_dir", type=Path)
    p.add_argument("--backend", required=True)
    p = sub.add_parser("finalize")
    p.add_argument("set_dir", type=Path)
    p.add_argument("--dump", type=Path, required=True)
    p.add_argument("--database-name", required=True)
    p.add_argument("--server-version")
    p.add_argument("--pg-dump-version")
    p.add_argument("--app-commit")
    p = sub.add_parser("verify")
    p.add_argument("set_dir", type=Path)
    p = sub.add_parser("describe")
    p.add_argument("set_dir", type=Path)
    p = sub.add_parser("storage-restore")
    p.add_argument("set_dir", type=Path)
    p.add_argument("--backend", required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--deep-verify", action="store_true")
    p = sub.add_parser("prune")
    p.add_argument("root")
    p.add_argument("--apply", action="store_true")
    for name, default in DEFAULT_RETENTION.items():
        p.add_argument(f"--{name}", type=int, default=default)
    args = parser.parse_args(argv)

    try:
        if args.command == "check-root":
            print(validate_root(args.root, create=args.create))
        elif args.command == "begin-set":
            set_dir, dump = begin_set(validate_root(args.root), args.database)
            print(set_dir)
            print(dump)
        elif args.command == "storage-backup":
            storage_backup(args.set_dir.resolve(), args.backend)
        elif args.command == "finalize":
            finalize(args.set_dir.resolve(), dump=args.dump.resolve(),
                     database_name=args.database_name, server_version=args.server_version,
                     pg_dump_version=args.pg_dump_version, app_commit=args.app_commit)
        elif args.command == "verify":
            problems = verify_set(args.set_dir.resolve())
            for problem in problems:
                log(f"FAIL: {problem}")
            if problems:
                return 1
            log(f"backup set {args.set_dir} verified: manifest, dump SHA256, pg_restore --list, "
                "inventory and every stored object")
        elif args.command == "describe":
            for key, value in describe(args.set_dir.resolve()).items():
                print(f"{key}={value}")
        elif args.command == "storage-restore":
            storage_restore(args.set_dir.resolve(), args.backend, force=args.force,
                            deep=args.deep_verify)
        elif args.command == "prune":
            prune(validate_root(args.root), apply=args.apply, daily=args.daily,
                  weekly=args.weekly, monthly=args.monthly)
    except BackupError as exc:
        log(f"ERROR: {exc}")
        return 1
    except Exception as exc:  # storage / OS errors: one line, never a traceback with locals
        log(f"ERROR: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
