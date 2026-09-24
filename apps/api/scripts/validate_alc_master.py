"""Validate an RCU Pune ALC master file without writing anything.

Canonical columns: RCU, DCU, SBU, ALC Code, ALC Name, Active. Reports missing fields,
invalid/duplicate ALC codes, duplicate rows, conflicting SBU/DCU (and DCU/RCU) mappings,
invalid Active values and — unless ``--offline`` — hierarchy values unknown to the database,
SBUs already placed under another DCU, and what an import would create / update. The
database session is always rolled back.

Usage:
    python -m scripts.validate_alc_master ../../data/RCU-PUNE-MASTER.xlsx
    python -m scripts.validate_alc_master master.csv --offline      # file checks only
    python -m scripts.validate_alc_master master.csv --json         # machine-readable report

Exit status: 0 when the file is valid, 1 when it has errors, 2 when it cannot be read.
"""

import argparse
import asyncio
import json
from pathlib import Path

from app.services import master_import


def print_report(report: dict) -> None:
    counts = report["counts"]
    print(f"Source rows (non-empty): {counts['total']}")
    print(
        f"  create: {counts['create']} | update: {counts['update']} | "
        f"unchanged: {counts['unchanged']} | invalid: {counts['invalid']}"
    )
    for row in report["invalid"]:
        print(f"    row {row['row']} {row['alc_code']!r}: {'; '.join(row['errors'])}")
    for row in report["warnings"]:
        print(f"    row {row['row']} {row['alc_code']!r} (warning): {'; '.join(row['warnings'])}")
    for row in report["rows"]:
        if row["action"] == "update":
            changes = ", ".join(f"{k}: {v[0]!r} -> {v[1]!r}" for k, v in row["changes"].items())
            print(f"    update {row['alc_code']}: {changes}")
    if report.get("sbu_links"):
        links = ", ".join(f"{x['sbu']} -> {x['dcu']}" for x in report["sbu_links"])
        print(f"  SBUs to place under a DCU: {links}")
    if report.get("not_in_source"):
        print(
            f"  Database ALCs not in this file (kept, never deleted): "
            f"{len(report['not_in_source'])}"
        )
    nashik = report.get("nashik")
    if nashik:
        before = ", ".join(f"{k}={v['total']}" for k, v in nashik["before"].items())
        print(f"  Nashik now:   {before} (total {nashik['before_total']})")
        if nashik["after"] is not None:
            after = ", ".join(f"{k}={v['total']}" for k, v in nashik["after"].items())
            flag = "CHANGED" if nashik["changed"] else "unchanged"
            print(f"  Nashik after: {after} (total {nashik['after_total']}) [{flag}]")


def offline_report(records) -> dict:
    master_import.check_file(records)
    invalid = [
        {"row": r.row, "alc_code": r.alc_code, "errors": list(r.errors)}
        for r in records
        if r.errors
    ]
    return {
        "counts": {
            "total": len(records),
            "create": 0,
            "update": 0,
            "unchanged": 0,
            "invalid": len(invalid),
        },
        "valid": not invalid,
        "rows": [],
        "invalid": invalid,
        "warnings": [
            {"row": r.row, "alc_code": r.alc_code, "warnings": r.warnings}
            for r in records
            if r.warnings
        ],
    }


async def run(path: Path, offline: bool, as_json: bool) -> int:
    try:
        records = master_import.parse_source(path.name, path.read_bytes())
    except master_import.MasterFileError as exc:
        print(f"Cannot read {path}: {exc}")
        return 2
    if offline:
        report = offline_report(records)
    else:
        from app.database import SessionLocal

        async with SessionLocal() as db:
            report = master_import.public(await master_import.validate(db, records))
            await db.rollback()
    if as_json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print_report(report)
        print("Valid." if report["valid"] else "INVALID: fix the rows above.")
        print("Validation only: no changes written.")
    return 0 if report["valid"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an RCU Pune ALC master file")
    parser.add_argument("path", type=Path)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Check the file alone (no database): fields, codes, duplicates, mappings, Active.",
    )
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.path, args.offline, args.json)))


if __name__ == "__main__":
    main()
