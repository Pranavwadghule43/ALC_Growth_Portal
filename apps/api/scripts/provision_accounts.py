"""Provision missing ALC or SBU login accounts (never ADMIN or DCU).

Dry run (default intent; validates everything and writes nothing):
    python -m scripts.provision_accounts --role alc --dry-run
    python -m scripts.provision_accounts --role sbu --dry-run

Real execution must be explicit and must restate the reviewed dry-run count:
    python -m scripts.provision_accounts --role alc --execute \\
        --expect-create 784 --out ~/alc-credentials/alc-accounts.csv

Execution rules:
* exactly one of ``--dry-run`` / ``--execute`` is required;
* ``--execute`` needs ``--expect-create N`` equal to the planned number of new accounts and
  ``--out PATH`` for the credential file;
* conflicts or broken hierarchy block execution unless ``--allow-partial`` is given (they are
  then skipped and reported); inactive records are always skipped;
* everything runs in one transaction. The credential file is written (never overwriting)
  before the commit and only renamed into place after it succeeds; on any failure the
  transaction is rolled back and the partial file deleted.
* on POSIX (Linux/macOS) the credential file is created owner-only and explicitly forced to
  mode 0600. On Windows, chmod cannot express that: the file inherits the NTFS permissions
  of its folder, so the CLI warns and the operator must choose a private folder.

The credential file holds one-time plaintext temporary passwords for distribution. It is
refused inside a git working tree unless git ignores that path. Password hashes are never
printed or written anywhere.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import subprocess
from collections import Counter
from pathlib import Path

from app.services import account_provisioning as ap

# POSIX mode bits (chmod 0600) are only meaningful on POSIX systems. On Windows ``os.chmod``
# can only toggle the read-only flag; access is governed by the folder's NTFS ACL.
POSIX_PERMISSIONS = os.name == "posix"
CREDENTIAL_FILE_MODE = 0o600
WINDOWS_PERMISSION_WARNING = (
    "WARNING: on Windows the credential file cannot be restricted to mode 0600 with chmod; "
    "it inherits the NTFS permissions of its folder. Write it to a private folder only you "
    "can read, treat it as sensitive, and delete it after distribution."
)

OUTPUT_COLUMNS = (
    "Login Identifier",
    "Username",
    "Display Name",
    "Role",
    "RCU",
    "DCU",
    "SBU",
    "ALC Code",
    "Temporary Password",
    "Must Change Password",
)


class OutputPathError(ValueError):
    pass


# --------------------------------------------------------------------------- #
# Credential output
# --------------------------------------------------------------------------- #
def _git_root(path: Path) -> Path | None:
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def check_output_path(out: Path) -> Path:
    """Validate the credential file location before anything is written to the database."""
    out = out.expanduser().resolve()
    if out.exists() or Path(f"{out}.partial").exists():
        raise OutputPathError(f"{out} already exists; credential files are never overwritten")
    if not out.parent.is_dir():
        raise OutputPathError(f"directory {out.parent} does not exist")
    if not os.access(out.parent, os.W_OK):
        raise OutputPathError(f"directory {out.parent} is not writable")
    root = _git_root(out.parent)
    if root is not None:
        try:
            ignored = (
                subprocess.run(
                    ["git", "-C", str(root), "check-ignore", "-q", str(out)],
                    capture_output=True,
                    timeout=10,
                ).returncode
                == 0
            )
        except (OSError, subprocess.SubprocessError):
            ignored = False
        if not ignored:
            raise OutputPathError(
                f"{out} is inside the git working tree {root} and is not git-ignored; "
                "write credentials outside the repository"
            )
    return out


def _cell(value: str) -> str:
    """Neutralise spreadsheet formula injection from master-data text."""
    return f"'{value}" if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def write_partial(out: Path, credentials: list[ap.Credential]) -> Path:
    """Write credentials to ``<out>.partial`` (exclusive create; owner-only 0600 on POSIX)
    and return its path."""
    partial = Path(f"{out}.partial")
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, CREDENTIAL_FILE_MODE)
    if POSIX_PERMISSIONS:
        # Enforce 0600 explicitly, before any secret is written, whatever the umask.
        os.fchmod(fd, CREDENTIAL_FILE_MODE)
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(OUTPUT_COLUMNS)
        for c in credentials:
            writer.writerow(
                [
                    c.login_identifier,
                    c.username,
                    _cell(c.display_name),
                    c.role,
                    _cell(c.rcu),
                    _cell(c.dcu),
                    _cell(c.sbu),
                    c.alc_code,
                    c.temporary_password,
                    "Yes",
                ]
            )
        fh.flush()
        os.fsync(fh.fileno())
    return partial


# --------------------------------------------------------------------------- #
# Reporting (no passwords, no hashes)
# --------------------------------------------------------------------------- #
def print_report(report: dict, *, show_creates: bool = False) -> None:
    counts = report["counts"]
    print(f"Role: {report['role']}")
    print(f"  total master records : {counts['total']}")
    print(f"  eligible             : {counts['eligible']}")
    print(f"  would create         : {counts['create']}")
    print(f"  existing / unchanged : {counts['existing']}")
    print(f"  inactive / skipped   : {counts['inactive']}")
    print(f"  conflicts            : {counts['conflict']}")
    print(f"  invalid hierarchy    : {counts['invalid']}")
    creates = Counter((i["dcu"], i["sbu"]) for i in report["items"] if i["action"] == "create")
    if creates:
        print("  would create by DCU / SBU:")
        for (dcu, sbu), n in sorted(creates.items()):
            print(f"    {dcu} / {sbu}: {n}")
    for item in report["items"]:
        action = item["action"]
        label = item["alc_code"] or item["sbu"]
        if action in ("conflict", "invalid", "inactive"):
            print(f"  {action.upper():<8} {label}: {item['reason']}")
        elif action == "existing" and (item.get("note") or report["role"] == "SBU"):
            note = f" ({item['note']})" if item.get("note") else ""
            print(f"  EXISTING {label}: {item['existing_username']}{note}")
        elif action == "create" and show_creates:
            print(
                f"  CREATE   {label}: login '{item['login_identifier']}' "
                f"username '{item['username']}'"
            )


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
async def run(args, session_factory=None) -> int:
    if session_factory is None:
        from app.database import SessionLocal as session_factory

    out = None
    if args.execute:
        try:
            out = check_output_path(args.out)
        except OutputPathError as exc:
            print(f"Refusing to run: {exc}")
            return 2
        if not POSIX_PERMISSIONS:
            print(WINDOWS_PERMISSION_WARNING)

    async with session_factory() as db:
        if args.dry_run:
            report = ap.public(await ap.plan(db, args.role))
            await db.rollback()
            print_report(report, show_creates=args.show_creates)
            print("DRY RUN: no accounts created, no passwords generated, nothing written.")
            return 0 if not (report["counts"]["conflict"] or report["counts"]["invalid"]) else 1

        partial = None
        try:
            report, credentials = await ap.provision(
                db,
                args.role,
                allow_partial=args.allow_partial,
                expect_create=args.expect_create,
            )
            partial = write_partial(out, credentials)
            await db.commit()
        except ap.ProvisioningBlocked as exc:
            await db.rollback()
            print_report(ap.public(exc.report))
            print(f"BLOCKED: {exc.reason}. Nothing was written.")
            return 1
        except BaseException:
            await db.rollback()
            if partial is not None:
                partial.unlink(missing_ok=True)
            print("FAILED: transaction rolled back; no accounts were created.")
            raise
        try:
            os.replace(partial, out)
        except OSError as exc:
            out = partial  # accounts exist; keep the credentials where they were written
            print(f"WARNING: could not rename the credential file ({exc}).")
        print_report(ap.public(report))
        print(
            f"CREATED {len(credentials)} {report['role']} account(s) "
            f"(must change password at first login). Batch {report['batch']}."
        )
        protection = "mode 0600" if POSIX_PERMISSIONS else "folder NTFS permissions"
        print(
            f"Temporary passwords written to {out} ({protection}). Distribute securely, then "
            "delete the file. It is not stored anywhere else."
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Provision missing ALC or SBU login accounts")
    parser.add_argument("--role", required=True, choices=sorted(ap.ROLES))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Validate and report; write nothing.")
    mode.add_argument("--execute", action="store_true", help="Really create the accounts.")
    parser.add_argument("--expect-create", type=int, help="Required with --execute.")
    parser.add_argument("--out", type=Path, help="Credential CSV (required with --execute).")
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="With --execute: skip conflicting/invalid records instead of refusing.",
    )
    parser.add_argument(
        "--show-creates", action="store_true", help="Dry run: list every account to create."
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.execute and (args.out is None or args.expect_create is None):
        parser.error("--execute requires --out PATH and --expect-create N")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
