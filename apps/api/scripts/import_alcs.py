"""Import or update the real ALC master list (ALC Code, ALC Name, SBU).

Reads a CSV or XLSX master file and upserts ALCs by ALC Code using the shared import
service. Only ALC Code, ALC Name and SBU are read; any other column (e.g. DCU) is ignored.
Existing ALCs are updated in place (name / SBU only) and never deleted; login accounts and
passwords are never touched. Every SBU value must already exist in the sbus table, otherwise
the import is blocked and the missing SBUs are reported.

Usage:
    python -m scripts.import_alcs ../../data/ALC-MASTER.csv
    python -m scripts.import_alcs ../../data/ALC-MASTER.xlsx --validate-only
"""
import argparse
import asyncio
from pathlib import Path

from app.database import SessionLocal
from app.services import alc_import


async def run(path: Path, validate_only: bool, replace: bool) -> int:
    content = path.read_bytes()
    records = alc_import.parse_source(path.name, content)
    async with SessionLocal() as db:
        report = await alc_import.validate(db, records)
        counts = report["counts"]
        print(f"Source rows (non-empty): {counts['total']}")
        print(
            f"  new: {counts['new']} | update: {counts['update']} | "
            f"unchanged: {counts['unchanged']} | invalid: {counts['invalid']}"
        )
        if report["duplicates"]:
            print(f"  duplicate ALC Codes in source: {report['duplicates']}")
        if report["missing_sbus"]:
            print(f"  MISSING SBU records (create these first): {report['missing_sbus']}")
        for row in report["invalid"]:
            print(f"    row {row['row']} {row['alc_code']!r}: {row['reason']}")

        if validate_only:
            print("Validation only: no changes written.")
            return 1 if counts["invalid"] else 0

        if counts["invalid"]:
            print("Import blocked: fix the invalid rows above. No changes written.")
            return 1

        if replace:
            # Hard reset the ALC domain first, then load the master fresh. Validation above
            # already confirmed every SBU exists, so the reset will not orphan the reload.
            removed = await alc_import.hard_reset_alc_domain(db)
            print("Hard reset (ALC domain wiped; ADMIN/SBU accounts and SBUs kept):")
            for table, n in removed.items():
                if n:
                    print(f"  removed {n} {table}")

        summary = await alc_import.perform(db, records)
        by_sbu = await alc_import.counts_by_sbu(db)
        orphans = await alc_import.orphan_codes(db, records)
        print(
            "Imported: "
            f"created={summary['created']} updated={summary['updated']} "
            f"unchanged={summary['unchanged']} skipped={summary['skipped']} "
            f"failed={summary['failed']}"
        )
        print("ALC counts by SBU:")
        for row in by_sbu:
            print(f"  {row['sbu'] or '(unassigned)'}: {row['alcs']}")
        if orphans:
            print(f"Database ALCs NOT in this master (kept, not deleted): {orphans}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Import or update the ALC master list")
    parser.add_argument("path", type=Path)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Parse and classify the source without writing to the database.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help=(
            "DESTRUCTIVE hard reset: wipe all ALC-domain data (ALCs, ALC login accounts, "
            "activities, evidence, partners, tasks, challenge progress) and load the master "
            "fresh. ADMIN/SBU accounts and the SBU master are kept."
        ),
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.path, args.validate_only, args.replace)))


if __name__ == "__main__":
    main()
