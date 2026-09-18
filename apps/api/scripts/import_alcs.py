import argparse
import asyncio
import csv
import re
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert

from app.database import SessionLocal
from app.enums import AlcStatus
from app.models import ALC

CODE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$")


async def import_file(path: Path) -> dict:
    summary = {"rows": 0, "inserted_or_updated": 0, "invalid": []}
    seen: set[str] = set()
    async with SessionLocal() as db:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or not {"ALC Code", "ALC Name"}.issubset(reader.fieldnames):
                raise ValueError("CSV must contain 'ALC Code' and 'ALC Name' columns")
            for line, row in enumerate(reader, start=2):
                summary["rows"] += 1
                code = (row.get("ALC Code") or "").strip()
                name = (row.get("ALC Name") or "").strip()
                if not CODE_PATTERN.fullmatch(code) or len(name) < 2:
                    summary["invalid"].append(
                        {"line": line, "alc_code": code, "reason": "Invalid code or missing name"}
                    )
                    continue
                if code in seen:
                    summary["invalid"].append(
                        {"line": line, "alc_code": code, "reason": "Duplicate code in file"}
                    )
                    continue
                seen.add(code)
                statement = (
                    insert(ALC)
                    .values(alc_code=code, alc_name=name, status=AlcStatus.ACTIVE)
                    .on_conflict_do_update(index_elements=[ALC.alc_code], set_={"alc_name": name})
                )
                await db.execute(statement)
                summary["inserted_or_updated"] += 1
        await db.commit()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Import or update the ALC master list")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    summary = asyncio.run(import_file(args.path))
    print(
        "Rows: {rows} | imported/updated: {imported_or_updated} | invalid: {invalid}".format(
            rows=summary["rows"],
            imported_or_updated=summary["inserted_or_updated"],
            invalid=len(summary["invalid"]),
        )
    )
    for issue in summary["invalid"]:
        print(f"Line {issue['line']}: {issue['alc_code']!r} - {issue['reason']}")


if __name__ == "__main__":
    main()
