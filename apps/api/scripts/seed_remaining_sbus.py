"""Seed the SBU master for DCU Ahilya Nagar, Pune North and Pune South.

Creates only the missing canonical SBUs (from ``app.services.master_mappings``) under the
existing RCU Pune DCUs. Idempotent. Stops without writing on any conflict (an SBU already
under another DCU), spelling variant, or ambiguity. Never touches ALCs, users, passwords,
activities, partners, evidence, reviews, revisions or the Nashik SBUs. Requires the RCU/DCU
master (``python -m scripts.seed_hierarchy`` or migration 20260923_0004) to exist.

Usage:
    python -m scripts.seed_remaining_sbus --dry-run
    python -m scripts.seed_remaining_sbus
"""

import argparse
import asyncio

from app.database import SessionLocal
from app.services import sbu_master


def print_plan(report: dict) -> None:
    for item in report["items"]:
        line = f"  {item['action']:<9} {item['dcu']:<17} {item['sbu']:<18} '{item['name']}'"
        if item.get("reason") or item.get("note"):
            line += f"  <- {item.get('reason') or item['note']}"
        print(line)
    counts = report["counts"]
    print(f"create: {counts['create']} | link: {counts['link']} | unchanged: {counts['unchanged']}")


def print_nashik(label: str, snapshot: dict) -> None:
    parts = [
        f"{code}={v['alcs']} ({v['dcu']})" if v else f"{code}=MISSING"
        for code, v in snapshot.items()
    ]
    total = sum(v["alcs"] for v in snapshot.values() if v)
    print(f"Nashik {label}: {', '.join(parts)} total={total}")


async def run(dry_run: bool) -> int:
    async with SessionLocal() as db:
        try:
            report = await sbu_master.seed_remaining_sbus(db, dry_run=dry_run)
        except sbu_master.SbuSeedBlocked as exc:
            await db.rollback()
            print_plan(exc.report)
            print("SBU seed BLOCKED. No changes written:")
            for blocker in exc.report["blockers"]:
                print(f"  - {blocker}")
            return 1
        print_plan(report)
        print_nashik("before", report["nashik_before"])
        if dry_run:
            await db.rollback()
            print("Dry run: no changes written.")
            return 0
        await db.commit()
        print_nashik("after ", report["nashik_after"])
        print("Hierarchy (RCU / DCU / SBU: ALCs):")
        for row in await sbu_master.hierarchy_tree(db):
            print(
                f"  {row['rcu'] or '-'} / {row['dcu'] or '(no DCU)'} / {row['sbu']}: {row['alcs']}"
            )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the remaining RCU Pune SBUs")
    parser.add_argument("--dry-run", action="store_true", help="Show the plan; write nothing.")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.dry_run)))


if __name__ == "__main__":
    main()
