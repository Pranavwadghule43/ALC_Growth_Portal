"""RCU Pune ALC master: validation and safe, idempotent import.

Canonical source columns (CSV or XLSX, header names matched case-insensitively)::

    RCU | DCU | SBU | ALC Code | ALC Name | Active

``ALC Code`` is the permanent identity of an ALC and is always handled as a string (leading
zeros are kept; spreadsheet numeric cells are rendered without ``.0``).

Validation never writes. It reports, per row, missing fields, invalid codes, duplicate ALC
codes, duplicate rows, SBU/DCU mappings that conflict inside the file or with the database,
hierarchy values that do not exist in the database, and invalid ``Active`` values.

The import is additive and idempotent:

* an ALC is matched by ``ALC Code``; a new code creates one ALC, an existing code is updated
  in place (name, SBU, active status only), so its database id — and with it every login,
  activity, partner, evidence, review and revision that references it — is preserved;
* nothing is ever deleted: ALCs absent from the source are only reported;
* RCU, DCU and SBU records are never created or renamed. An SBU that has no DCU yet is linked
  to the DCU named in the source; an SBU already placed under another DCU is a conflict;
* login accounts and passwords are never read or written;
* the whole import runs in one transaction and is refused, writing nothing, if validation
  reports any error. Re-running the same file changes nothing.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import AlcStatus
from app.models import ALC, DCU, RCU, SBU
from app.services.alc_import import CODE_PATTERN, _clean_code, _clean_text, _row_is_empty
from app.services.audit import record_audit
from app.services.hierarchy import SBU_DCU_LINKS

CANONICAL_COLUMNS = ("RCU", "DCU", "SBU", "ALC Code", "ALC Name", "Active")
ACTIVE_VALUES = {
    "yes": AlcStatus.ACTIVE,
    "y": AlcStatus.ACTIVE,
    "true": AlcStatus.ACTIVE,
    "1": AlcStatus.ACTIVE,
    "active": AlcStatus.ACTIVE,
    "no": AlcStatus.INACTIVE,
    "n": AlcStatus.INACTIVE,
    "false": AlcStatus.INACTIVE,
    "0": AlcStatus.INACTIVE,
    "inactive": AlcStatus.INACTIVE,
}
# SBUs whose DCU placement is already fixed (SBU 4 / 6 / 7 → DCU Nashik).
NASHIK_SBUS = tuple(sorted(code for code, dcu in SBU_DCU_LINKS.items() if dcu == "DCU_NASHIK"))


class MasterFileError(ValueError):
    """The source cannot be read as a canonical master (bad header, empty file, ...)."""


class MasterImportBlocked(Exception):
    """Validation found errors; carries the full report. Nothing was written."""

    def __init__(self, report: dict):
        self.report = report
        super().__init__("ALC master import blocked: the source has validation errors")


@dataclass
class MasterRecord:
    row: int
    rcu: str
    dcu: str
    sbu: str
    alc_code: str
    alc_name: str
    active: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def fields(self) -> tuple[str, ...]:
        return (self.rcu, self.dcu, self.sbu, self.alc_code, self.alc_name, self.active)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _norm(value: str) -> str:
    """Comparison key for hierarchy names: case, spacing, ``_`` and ``-`` are ignored, so
    ``DCU_NASHIK``, ``DCU Nashik`` and ``dcu-nashik`` compare equal."""
    return re.sub(r"[^0-9a-z]", "", value.lower())


def _key(value: str, prefix: str) -> str:
    """``_norm`` with the level prefix dropped, so ``DCU Nashik`` and ``Nashik`` match."""
    key = _norm(value)
    return key[len(prefix) :] if key.startswith(prefix) and len(key) > len(prefix) else key


def _records(header, data_rows, numeric_code_rows: set[int] | None = None) -> list[MasterRecord]:
    positions: dict[str, int] = {}
    duplicates: list[str] = []
    for position, name in enumerate(header):
        if name is None or str(name).strip() == "":
            continue
        key = " ".join(str(name).split()).lower()
        if key in positions:
            duplicates.append(str(name).strip())
        positions[key] = position
    if duplicates:
        raise MasterFileError(f"Duplicate column(s) in header: {', '.join(duplicates)}")
    missing = [c for c in CANONICAL_COLUMNS if c.lower() not in positions]
    if missing:
        raise MasterFileError(
            f"Source must contain columns {', '.join(CANONICAL_COLUMNS)} "
            f"(missing: {', '.join(missing)})"
        )
    index = {c: positions[c.lower()] for c in CANONICAL_COLUMNS}

    def cell(values, column):
        pos = index[column]
        return values[pos] if pos < len(values) else None

    records: list[MasterRecord] = []
    for offset, values in enumerate(data_rows, start=2):  # row 1 is the header
        if _row_is_empty(values):
            continue
        record = MasterRecord(
            row=offset,
            rcu=_clean_text(cell(values, "RCU")),
            dcu=_clean_text(cell(values, "DCU")),
            sbu=_clean_text(cell(values, "SBU")),
            alc_code=_clean_code(cell(values, "ALC Code")),
            alc_name=" ".join(_clean_text(cell(values, "ALC Name")).split()),
            active=_clean_text(cell(values, "Active")),
        )
        if numeric_code_rows and offset in numeric_code_rows:
            record.warnings.append(
                "ALC Code is a numeric spreadsheet cell; any leading zeros are already lost. "
                "Format the column as Text"
            )
        records.append(record)
    return records


def parse_csv(content: bytes) -> list[MasterRecord]:
    rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    if not rows:
        raise MasterFileError("The source file is empty")
    return _records(rows[0], rows[1:])


def parse_xlsx(content: bytes) -> list[MasterRecord]:
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        rows = list(workbook.active.iter_rows(values_only=True))
    finally:
        workbook.close()
    if not rows:
        raise MasterFileError("The source file is empty")
    header = [None if h is None else str(h) for h in rows[0]]
    code_pos = next(
        (i for i, h in enumerate(header) if h and " ".join(h.split()).lower() == "alc code"),
        None,
    )
    numeric = set()
    if code_pos is not None:
        for offset, values in enumerate(rows[1:], start=2):
            value = values[code_pos] if code_pos < len(values) else None
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric.add(offset)
    return _records(header, rows[1:], numeric)


def parse_source(filename: str | None, content: bytes) -> list[MasterRecord]:
    name = (filename or "").lower()
    if name.endswith(".xlsx") or (not name.endswith((".csv", ".txt")) and content[:2] == b"PK"):
        return parse_xlsx(content)
    return parse_csv(content)


# --------------------------------------------------------------------------- #
# File-level checks (need no database)
# --------------------------------------------------------------------------- #
def check_file(records: list[MasterRecord]) -> None:
    """Annotate each record with errors that can be found from the file alone."""
    for r in records:
        r.errors.clear()  # safe to validate the same records more than once
        for column, value in zip(CANONICAL_COLUMNS, r.fields(), strict=True):
            if not value:
                r.errors.append(f"Missing {column}")
        if r.alc_code:
            if re.fullmatch(r"\d+(\.\d+)?[eE][+-]?\d+", r.alc_code):
                r.errors.append("ALC Code is in scientific notation; export the column as Text")
            elif not CODE_PATTERN.fullmatch(r.alc_code):
                r.errors.append(f"Invalid ALC Code '{r.alc_code}'")
        if r.active and r.active.lower() not in ACTIVE_VALUES:
            r.errors.append(
                f"Invalid Active value '{r.active}' (use Yes/No, Y/N, True/False, 1/0, "
                "Active/Inactive)"
            )

    # Duplicate rows (every field identical) and duplicate ALC codes with differing data.
    first_row: dict[tuple, int] = {}
    first_code: dict[str, MasterRecord] = {}
    for r in records:
        key = tuple(_norm(v) if i != 3 else v for i, v in enumerate(r.fields()))
        if key in first_row:
            r.errors.append(f"Duplicate row (identical to row {first_row[key]})")
            continue
        first_row[key] = r.row
        if not r.alc_code:
            continue
        code_key = r.alc_code.lower()
        if code_key in first_code:
            other = first_code[code_key]
            r.errors.append(f"Duplicate ALC Code '{r.alc_code}' (also row {other.row})")
            msg = f"Duplicate ALC Code '{other.alc_code}' (also row {r.row})"
            if msg not in other.errors:
                other.errors.append(msg)
        else:
            first_code[code_key] = r

    # One SBU belongs to exactly one DCU, and one DCU to exactly one RCU, across the file.
    _flag_conflicts(records, lambda r: r.sbu, lambda r: r.dcu, ("SBU", "DCU"))
    _flag_conflicts(records, lambda r: r.dcu, lambda r: r.rcu, ("DCU", "RCU"))


def _flag_conflicts(records, child, parent, label) -> None:
    child_level, parent_level = label
    cp, pp = child_level.lower(), parent_level.lower()
    parents: dict[str, set[str]] = defaultdict(set)
    names: dict[str, set[str]] = defaultdict(set)
    for r in records:
        if child(r) and parent(r):
            parents[_key(child(r), cp)].add(_key(parent(r), pp))
            names[_key(child(r), cp)].add(parent(r))
    for r in records:
        key = _key(child(r), cp) if child(r) else None
        if key and len(parents.get(key, ())) > 1:
            r.errors.append(
                f"Conflicting {child_level}/{parent_level} mapping: {child_level} "
                f"'{child(r)}' appears under {parent_level}s {', '.join(sorted(names[key]))}"
            )


# --------------------------------------------------------------------------- #
# Database lookups
# --------------------------------------------------------------------------- #
def _lookup(items, prefix: str) -> dict[str, object]:
    """Map every accepted spelling (code, name, and either without the level prefix) to the
    record. A spelling shared by two different records maps to ``None`` (ambiguous)."""
    table: dict[str, object] = {}
    for item in items:
        keys = {_norm(item.code), _norm(item.name)}
        keys |= {_key(item.code, prefix), _key(item.name, prefix)}
        for key in keys:
            if key in table and table[key] is not item:
                table[key] = None
            else:
                table[key] = item
    return table


@dataclass
class _Hierarchy:
    rcus: dict
    dcus: dict
    sbus: dict


async def _hierarchy(db: AsyncSession) -> _Hierarchy:
    return _Hierarchy(
        rcus=_lookup((await db.scalars(select(RCU))).all(), "rcu"),
        dcus=_lookup((await db.scalars(select(DCU))).all(), "dcu"),
        sbus=_lookup((await db.scalars(select(SBU))).all(), "sbu"),
    )


def _resolve(table: dict, value: str, level: str, errors: list[str]):
    key = _norm(value)
    if key not in table:
        key = _key(value, level.lower())
    if key not in table:
        errors.append(f"Unknown {level} '{value}'")
        return None
    if table[key] is None:
        errors.append(f"Ambiguous {level} '{value}' matches more than one {level}")
        return None
    return table[key]


# --------------------------------------------------------------------------- #
# Validation (never writes)
# --------------------------------------------------------------------------- #
async def validate(db: AsyncSession, records: list[MasterRecord]) -> dict:
    """Classify every row as ``create`` / ``update`` / ``unchanged`` / ``invalid`` against the
    current database, and describe exactly what an import would change."""
    check_file(records)
    hierarchy = await _hierarchy(db)
    existing_alcs = (await db.scalars(select(ALC))).all()
    by_code = {a.alc_code: a for a in existing_alcs}
    by_lower = {a.alc_code.lower(): a for a in existing_alcs}
    by_stripped = defaultdict(list)
    for a in existing_alcs:
        by_stripped[a.alc_code.lstrip("0").lower()].append(a)
    sbu_codes = {s.id: s.code for s in (await db.scalars(select(SBU))).all()}

    plan: list[dict] = []
    sbu_links: dict = {}
    for r in records:
        rcu = _resolve(hierarchy.rcus, r.rcu, "RCU", r.errors) if r.rcu else None
        dcu = _resolve(hierarchy.dcus, r.dcu, "DCU", r.errors) if r.dcu else None
        sbu = _resolve(hierarchy.sbus, r.sbu, "SBU", r.errors) if r.sbu else None
        if rcu is not None and dcu is not None and dcu.rcu_id != rcu.id:
            r.errors.append(f"DCU '{r.dcu}' does not belong to RCU '{r.rcu}'")
        if sbu is not None and dcu is not None:
            if sbu.dcu_id is None:
                # Conflicting DCUs for one SBU were already flagged by check_file.
                sbu_links.setdefault(sbu.id, (sbu, dcu))
            elif sbu.dcu_id != dcu.id:
                r.errors.append(
                    f"SBU '{sbu.code}' is already assigned to a different DCU in the database "
                    f"(not '{r.dcu}')"
                )

        alc = by_code.get(r.alc_code) if r.alc_code else None
        if r.alc_code and alc is None:
            near = by_lower.get(r.alc_code.lower())
            if near is not None:
                r.errors.append(
                    f"ALC Code '{r.alc_code}' differs only in letter case from existing "
                    f"'{near.alc_code}'"
                )
            others = [a.alc_code for a in by_stripped.get(r.alc_code.lstrip("0").lower(), [])]
            if others and near is None:
                r.errors.append(
                    f"ALC Code '{r.alc_code}' differs only in leading zeros from existing "
                    f"'{others[0]}'"
                )

        entry = {
            "row": r.row,
            "alc_code": r.alc_code,
            "alc_name": r.alc_name,
            "rcu": r.rcu,
            "dcu": r.dcu,
            "sbu": r.sbu,
            "active": r.active,
        }
        if r.warnings:
            entry["warnings"] = list(r.warnings)
        if r.errors:
            entry.update(action="invalid", errors=list(r.errors))
            plan.append(entry)
            continue

        status = ACTIVE_VALUES[r.active.lower()]
        if alc is None:
            entry.update(action="create", sbu=sbu.code, status=status.value)
        else:
            changes = {}
            if alc.alc_name != r.alc_name:
                changes["alc_name"] = [alc.alc_name, r.alc_name]
            if alc.sbu_id != sbu.id:
                changes["sbu"] = [sbu_codes.get(alc.sbu_id), sbu.code]
            if alc.status != status:
                changes["status"] = [alc.status.value, status.value]
            entry.update(
                action="update" if changes else "unchanged",
                sbu=sbu.code,
                status=status.value,
                alc_id=str(alc.id),
            )
            if changes:
                entry["changes"] = changes
        entry["_sbu_id"] = sbu.id
        plan.append(entry)

    counts = Counter(e["action"] for e in plan)
    invalid = [e for e in plan if e["action"] == "invalid"]
    source_codes = {r.alc_code for r in records if r.alc_code}
    projected = _project(existing_alcs, sbu_codes, plan) if not invalid else None
    report = {
        "counts": {
            "total": len(records),
            "create": counts["create"],
            "update": counts["update"],
            "unchanged": counts["unchanged"],
            "invalid": counts["invalid"],
        },
        "valid": not invalid,
        "rows": [{k: v for k, v in e.items() if not k.startswith("_")} for e in plan],
        "invalid": [{k: v for k, v in e.items() if not k.startswith("_")} for e in invalid],
        "warnings": [
            {"row": e["row"], "alc_code": e["alc_code"], "warnings": e["warnings"]}
            for e in plan
            if e.get("warnings")
        ],
        "sbu_links": sorted(
            ({"sbu": s.code, "dcu": d.code} for s, d in sbu_links.values()),
            key=lambda x: x["sbu"],
        ),
        "not_in_source": sorted(
            a.alc_code for a in existing_alcs if a.alc_code not in source_codes
        ),
        "nashik": _nashik(existing_alcs, sbu_codes, projected),
    }
    report["_plan"] = plan
    report["_sbu_links"] = list(sbu_links.values())
    return report


def _project(existing_alcs, sbu_codes, plan) -> dict[str, dict]:
    """Per-ALC (sbu code, status) after the plan is applied; ALCs absent from the source
    keep their current placement."""
    state = {a.alc_code: (sbu_codes.get(a.sbu_id), a.status.value) for a in existing_alcs}
    for e in plan:
        state[e["alc_code"]] = (e["sbu"], e["status"])
    return state


def _sbu_counts(state) -> dict[str, dict[str, int]]:
    counts = {code: {"total": 0, "active": 0} for code in NASHIK_SBUS}
    keys = {_norm(code): code for code in NASHIK_SBUS}
    for sbu, status in state.values():
        code = keys.get(_norm(sbu)) if sbu else None
        if code:
            counts[code]["total"] += 1
            counts[code]["active"] += status == AlcStatus.ACTIVE.value
    return counts


def _nashik(existing_alcs, sbu_codes, projected) -> dict:
    before = _sbu_counts(
        {a.alc_code: (sbu_codes.get(a.sbu_id), a.status.value) for a in existing_alcs}
    )
    result = {
        "before": before,
        "before_total": sum(c["total"] for c in before.values()),
        "after": None,
        "after_total": None,
        "changed": None,
    }
    if projected is not None:
        after = _sbu_counts(projected)
        result.update(
            after=after,
            after_total=sum(c["total"] for c in after.values()),
            changed=after != before,
        )
    return result


def public(report: dict) -> dict:
    """The report without internal planning keys (safe to print or serialise)."""
    return {k: v for k, v in report.items() if not k.startswith("_")}


# --------------------------------------------------------------------------- #
# Import (single transaction; refuses to write anything if validation fails)
# --------------------------------------------------------------------------- #
async def perform(
    db: AsyncSession,
    records: list[MasterRecord],
    *,
    allow_nashik_change: bool = False,
    source: str | None = None,
) -> dict:
    report = await validate(db, records)
    if not report["valid"]:
        raise MasterImportBlocked(public(report))
    if report["nashik"]["changed"] and not allow_nashik_change:
        report["valid"] = False
        report["blocked_reason"] = (
            "The import would change the ALC count of a Nashik SBU "
            f"({', '.join(NASHIK_SBUS)}). Re-run with allow_nashik_change to accept it."
        )
        raise MasterImportBlocked(public(report))

    created = updated = unchanged = 0
    changed_codes: list[dict] = []
    try:
        for sbu, dcu in report["_sbu_links"]:
            sbu.dcu_id = dcu.id  # only SBUs that had no DCU; never re-parented
        codes = [e["alc_code"] for e in report["_plan"]]
        found = await db.scalars(select(ALC).where(ALC.alc_code.in_(codes)))
        alcs = {a.alc_code: a for a in found.all()}
        for e in report["_plan"]:
            status = AlcStatus(e["status"])
            alc = alcs.get(e["alc_code"])
            if alc is None:
                db.add(
                    ALC(
                        alc_code=e["alc_code"],
                        alc_name=e["alc_name"],
                        sbu_id=e["_sbu_id"],
                        status=status,
                    )
                )
                created += 1
                changed_codes.append({"alc_code": e["alc_code"], "action": "create"})
                continue
            if e["action"] == "unchanged":
                unchanged += 1
                continue
            # In-place update: the ALC keeps its id, so users, activities, partners,
            # evidence, reviews and revisions stay attached to it.
            alc.alc_name = e["alc_name"]
            alc.sbu_id = e["_sbu_id"]
            alc.status = status
            updated += 1
            changed_codes.append(
                {"alc_code": e["alc_code"], "action": "update", "changes": e["changes"]}
            )
        if changed_codes or report["_sbu_links"]:
            await record_audit(
                db,
                "ALC_MASTER_IMPORT",
                "ALC",
                metadata={
                    "source": source,
                    "created": created,
                    "updated": updated,
                    "unchanged": unchanged,
                    "sbu_links": report["sbu_links"],
                    "changes": changed_codes,
                },
            )
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return {
        "total": len(records),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "sbus_linked": report["sbu_links"],
        "not_in_source": report["not_in_source"],
        "nashik": report["nashik"],
    }


async def hierarchy_counts(db: AsyncSession) -> list[dict]:
    """ALC counts per RCU / DCU / SBU (total and active), including unplaced SBUs."""
    rows = (
        await db.execute(
            select(
                RCU.code,
                DCU.code,
                SBU.code,
                func.count(ALC.id),
                func.count(ALC.id).filter(ALC.status == AlcStatus.ACTIVE),
            )
            .select_from(SBU)
            .outerjoin(DCU, SBU.dcu_id == DCU.id)
            .outerjoin(RCU, DCU.rcu_id == RCU.id)
            .outerjoin(ALC, ALC.sbu_id == SBU.id)
            .group_by(RCU.code, DCU.code, SBU.code)
            .order_by(RCU.code, DCU.code, SBU.code)
        )
    ).all()
    return [
        {"rcu": rcu, "dcu": dcu, "sbu": sbu, "alcs": total, "active": active}
        for rcu, dcu, sbu, total, active in rows
    ]
