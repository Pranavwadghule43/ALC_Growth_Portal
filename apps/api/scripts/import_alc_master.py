"""Safely import the RCU Pune ALC master (RCU, DCU, SBU, ALC Code, ALC Name, Active).

Idempotent and additive: ALCs are matched by ALC Code, created when new and updated in place
(name, SBU, active status) when they exist, so every ALC keeps its database id together with
its logins, activities, partners, evidence, reviews and revisions. Nothing is deleted, no
RCU/DCU/SBU is created, no login account or password is touched. SBUs without a DCU are placed
under the DCU the file names; an SBU already under another DCU blocks the import.

The whole file is validated first; any error blocks the import and nothing is written. The
import is also refused if it would change the ALC count of a Nashik SBU (SBU 4 / 6 / 7)
unless ``--allow-nashik-change`` is given. Re-running the same file changes nothing.

Usage:
    python -m scripts.import_alc_master ../../data/RCU-PUNE-MASTER.xlsx --dry-run
    python -m scripts.import_alc_master ../../data/RCU-PUNE-MASTER.xlsx
"""

import argparse
import asyncio
from pathlib import Path

from app.database import SessionLocal
from app.services import master_import
from scripts.validate_alc_master import print_report


async def run(path: Path, dry_run: bool, allow_nashik_change: bool) -> int:
    try:
        records = master_import.parse_source(path.name, path.read_bytes())
    except master_import.MasterFileError as exc:
        print(f"Cannot read {path}: {exc}")
        return 2

    async with SessionLocal() as db:
        if dry_run:
            report = master_import.public(await master_import.validate(db, records))
            await db.rollback()
            print_report(report)
            print("Dry run: no changes written.")
            return 0 if report["valid"] else 1
        try:
            summary = await master_import.perform(
                db, records, allow_nashik_change=allow_nashik_change, source=path.name
            )
        except master_import.MasterImportBlocked as exc:
            print_report(exc.report)
            if exc.report.get("blocked_reason"):
                print(exc.report["blocked_reason"])
            print("Import blocked. No changes written.")
            return 1

        print(
            f"Imported {summary['total']} rows: created={summary['created']} "
            f"updated={summary['updated']} unchanged={summary['unchanged']}"
        )
        for link in summary["sbus_linked"]:
            print(f"  placed {link['sbu']} under {link['dcu']}")
        if summary["not_in_source"]:
            print(
                f"  {len(summary['not_in_source'])} database ALCs are not in this file "
                "(kept, not deleted)"
            )
        print("ALC counts (RCU / DCU / SBU: total, active):")
        for row in await master_import.hierarchy_counts(db):
            print(
                f"  {row['rcu'] or '-'} / {row['dcu'] or '(no DCU)'} / {row['sbu']}: "
                f"{row['alcs']}, {row['active']}"
            )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the RCU Pune ALC master")
    parser.add_argument("path", type=Path)
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate and show the plan; write nothing."
    )
    parser.add_argument(
        "--allow-nashik-change",
        action="store_true",
        help="Permit an import that changes the ALC count of SBU 4 / 6 / 7 (Nashik).",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.path, args.dry_run, args.allow_nashik_change)))


if __name__ == "__main__":
    main()
