"""Build the canonical RCU Pune ALC master from the DCU source spreadsheets.

Reads source files with the columns ``ALC Code, ALC Name, DCU, SBU`` (XLSX or CSV) and writes
the six-column canonical master ``RCU, DCU, SBU, ALC Code, ALC Name, Active`` expected by
``scripts.validate_alc_master`` / ``scripts.import_alc_master``. Never touches a database.

* ``ALC Code`` stays text. Numeric spreadsheet cells are rendered as exact integers (never as
  a float such as ``17210005.0``); a zero-padded number format (e.g. ``00000000``) restores
  its leading zeros. Text codes are kept as written.
* ``ALC Name`` is copied exactly as written in the source.
* DCU and SBU values are normalised only through the confirmed mappings in
  ``app.services.master_mappings``; anything else is reported as unresolved.
* The sources carry no active flag, so ``Active`` comes from the required, explicit
  ``--default-active`` option and the report says so.
* Blank codes/names, invalid codes, duplicate ALC codes, duplicate rows, unresolved mappings
  and per-SBU counts that differ from the confirmed counts are errors; the output file is
  not written while any error remains. The finished file is re-checked with the Phase 3A
  file validator before it is written.

Usage (from apps/api):
    python -m scripts.build_rcu_master ../../data/Ahilya_Nagar.xlsx ../../data/Pune.xlsx \\
        --default-active yes --out ../../data/RCU-PUNE-NEW-ALCS.csv
    # complete 784-row master, adding the already-imported Nashik list:
    python -m scripts.build_rcu_master ../../data/Ahilya_Nagar.xlsx ../../data/Pune.xlsx \\
        --include-nashik ../../data/ALC-MASTER.csv --default-active yes --out full.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from app.services import master_import
from app.services.alc_import import CODE_PATTERN
from app.services.master_mappings import (
    DCU_CODES,
    EXPECTED_ALC_COUNTS,
    NEW_DCUS,
    RCU_CANONICAL,
    SBUS_BY_DCU,
    canonical_dcu,
    canonical_sbu,
)

SOURCE_COLUMNS = ("ALC Code", "ALC Name", "DCU", "SBU")
ACTIVE_CHOICES = {"yes": "Yes", "no": "No"}


class SourceError(ValueError):
    """A source file cannot be read (missing columns, empty, unsupported)."""


@dataclass
class SourceRow:
    source: str
    row: int
    alc_code: str
    alc_name: str
    dcu: str
    sbu: str
    numeric_code: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def where(self) -> str:
        return f"{self.source} row {self.row}"


@dataclass
class BuildResult:
    rows: list[dict]
    errors: list[str]
    warnings: list[str]
    notes: list[str]
    counts: dict[str, dict[str, int]]
    active: str
    csv_bytes: bytes = b""

    @property
    def ok(self) -> bool:
        return not self.errors


# --------------------------------------------------------------------------- #
# Reading sources
# --------------------------------------------------------------------------- #
def _code(value, number_format: str | None = None) -> tuple[str, bool, str | None]:
    """(code text, came-from-numeric-cell, error). Never renders a float."""
    if value is None:
        return "", False, None
    if isinstance(value, bool):
        return str(value), False, "ALC Code is a TRUE/FALSE cell"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not value.is_integer():
            return repr(value), True, f"ALC Code {value!r} is not a whole number"
        if abs(value) >= 2**53:
            return repr(value), True, "ALC Code number is too large to be exact; use text"
        text = str(int(value))
        if number_format and re.fullmatch(r"0+", number_format):
            text = text.zfill(len(number_format))  # displayed leading zeros
        return text, True, None
    return str(value).strip(), False, None


def _text(value) -> str:
    return "" if value is None else str(value)


def _header_index(header, source: str) -> dict[str, int]:
    index = {}
    for position, name in enumerate(header):
        if name is not None and str(name).strip():
            index.setdefault(" ".join(str(name).split()).casefold(), position)
    missing = [c for c in SOURCE_COLUMNS if c.casefold() not in index]
    if missing:
        raise SourceError(f"{source}: missing column(s) {', '.join(missing)}")
    return {c: index[c.casefold()] for c in SOURCE_COLUMNS}


def read_xlsx(content: bytes, source: str) -> list[SourceRow]:
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    try:
        sheet = workbook.active
        rows = list(sheet.iter_rows())
    finally:
        workbook.close()
    if not rows:
        raise SourceError(f"{source}: empty workbook")
    index = _header_index([c.value for c in rows[0]], source)
    out = []
    for number, cells in enumerate(rows[1:], start=2):
        if all(c.value is None or str(c.value).strip() == "" for c in cells):
            continue

        def cell(column, cells=cells):
            pos = index[column]
            return cells[pos] if pos < len(cells) else None

        code_cell = cell("ALC Code")
        code, numeric, error = _code(
            code_cell.value if code_cell is not None else None,
            code_cell.number_format if code_cell is not None else None,
        )
        row = SourceRow(
            source=source,
            row=number,
            alc_code=code,
            alc_name=_text(cell("ALC Name").value if cell("ALC Name") is not None else None),
            dcu=_text(cell("DCU").value if cell("DCU") is not None else None).strip(),
            sbu=_text(cell("SBU").value if cell("SBU") is not None else None).strip(),
            numeric_code=numeric,
        )
        if error:
            row.errors.append(error)
        out.append(row)
    return out


def read_csv(content: bytes, source: str) -> list[SourceRow]:
    rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    if not rows:
        raise SourceError(f"{source}: empty file")
    index = _header_index(rows[0], source)
    out = []
    for number, values in enumerate(rows[1:], start=2):
        if not any(v.strip() for v in values):
            continue

        def cell(column, values=values):
            pos = index[column]
            return values[pos] if pos < len(values) else ""

        out.append(
            SourceRow(
                source=source,
                row=number,
                alc_code=cell("ALC Code").strip(),
                alc_name=cell("ALC Name"),
                dcu=cell("DCU").strip(),
                sbu=cell("SBU").strip(),
            )
        )
    return out


def read_source(path: Path) -> list[SourceRow]:
    content = path.read_bytes()
    if path.suffix.lower() == ".xlsx":
        return read_xlsx(content, path.name)
    if path.suffix.lower() in (".csv", ".txt"):
        return read_csv(content, path.name)
    raise SourceError(f"{path.name}: unsupported file type (use .xlsx or .csv)")


def read_nashik(path: Path) -> list[SourceRow]:
    """The already-imported Nashik list (``ALC Code, ALC Name, SBU``), as DCU Nashik rows."""
    rows = list(csv.DictReader(io.StringIO(path.read_bytes().decode("utf-8-sig"))))
    return [
        SourceRow(
            source=path.name,
            row=number,
            alc_code=(r.get("ALC Code") or "").strip(),
            alc_name=r.get("ALC Name") or "",
            dcu="Nashik",
            sbu=(r.get("SBU") or "").strip(),
        )
        for number, r in enumerate(rows, start=2)
        if any((v or "").strip() for v in r.values())
    ]


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #
def build(
    rows: list[SourceRow],
    default_active: str,
    *,
    include_nashik: bool = False,
    allow_count_mismatch: bool = False,
) -> BuildResult:
    """Normalise ``rows`` into canonical master rows and check them. Writes nothing."""
    active = ACTIVE_CHOICES.get((default_active or "").strip().lower())
    if active is None:
        raise ValueError("default_active must be 'yes' or 'no'")
    in_scope = NEW_DCUS + (("Nashik",) if include_nashik else ())
    errors: list[str] = []
    warnings: list[str] = []
    mapped: Counter = Counter()
    numeric_cells = 0
    canonical: list[tuple[SourceRow, dict]] = []

    for r in rows:
        numeric_cells += r.numeric_code
        if not r.alc_code:
            r.errors.append("blank ALC Code")
        elif not CODE_PATTERN.fullmatch(r.alc_code):
            r.errors.append(f"invalid ALC Code '{r.alc_code}'")
        if not r.alc_name.strip():
            r.errors.append("blank ALC Name")
        elif r.alc_name != r.alc_name.strip() or "  " in r.alc_name:
            warnings.append(f"{r.where}: ALC Name has extra spaces (kept exactly as written)")
        dcu = canonical_dcu(r.dcu)
        sbu = None
        if not r.dcu:
            r.errors.append("blank DCU")
        elif dcu is None:
            r.errors.append(f"unresolved DCU '{r.dcu}'")
        elif dcu not in in_scope:
            r.errors.append(f"DCU '{dcu}' is not part of this build (Nashik is already imported)")
        if not r.sbu:
            r.errors.append("blank SBU")
        elif dcu in in_scope:
            sbu = canonical_sbu(dcu, r.sbu)
            if sbu is None:
                r.errors.append(f"unresolved SBU '{r.sbu}' for DCU '{dcu}'")
            else:
                mapped[(dcu, r.dcu, r.sbu, sbu)] += 1
        canonical.append(
            (
                r,
                {
                    "RCU": RCU_CANONICAL,
                    "DCU": dcu or "",
                    "SBU": sbu or "",
                    "ALC Code": r.alc_code,
                    "ALC Name": r.alc_name,
                    "Active": active,
                },
            )
        )

    # Duplicate rows (identical source values) and duplicate ALC codes (case-insensitive).
    seen_rows: dict[tuple, SourceRow] = {}
    seen_codes: dict[str, SourceRow] = {}
    for r in rows:
        key = (r.alc_code, r.alc_name, r.dcu.casefold(), r.sbu.casefold())
        if key in seen_rows:
            r.errors.append(f"duplicate row (identical to {seen_rows[key].where})")
            continue
        seen_rows[key] = r
        if not r.alc_code:
            continue
        first = seen_codes.setdefault(r.alc_code.casefold(), r)
        if first is not r:
            r.errors.append(f"duplicate ALC Code '{r.alc_code}' (also {first.where})")
            message = f"duplicate ALC Code '{first.alc_code}' (also {r.where})"
            if message not in first.errors:
                first.errors.append(message)

    for r in rows:
        errors.extend(f"{r.where} [{r.alc_code or '-'}]: {e}" for e in r.errors)

    counts: dict[str, dict[str, int]] = {d: {s: 0 for s in SBUS_BY_DCU[d]} for d in in_scope}
    for r, out in canonical:
        if not r.errors:
            counts[out["DCU"]][out["SBU"]] += 1
    for dcu in in_scope:
        for sbu, expected in EXPECTED_ALC_COUNTS[dcu].items():
            if counts[dcu][sbu] != expected:
                message = (
                    f"count mismatch: {dcu} / {sbu} has {counts[dcu][sbu]} ALCs, "
                    f"expected {expected}"
                )
                (warnings if allow_count_mismatch else errors).append(message)

    notes = [
        f"Active='{active}' for all {len(rows)} rows comes from --default-active "
        f"{active.lower()}, NOT from the source spreadsheets (they have no active column)."
    ]
    if numeric_cells:
        notes.append(
            f"{numeric_cells} ALC Codes were numeric spreadsheet cells; written as exact "
            "integer text (no '.0'). Leading zeros can only be restored from a zero-padded "
            "cell format."
        )
    by_mapping = defaultdict(int)
    for (dcu, source_dcu, source_sbu, sbu), n in sorted(mapped.items()):
        if source_dcu != dcu:
            by_mapping[f"DCU '{source_dcu}' -> '{dcu}'"] += n
        if source_sbu != sbu:
            by_mapping[f"SBU '{source_sbu}' -> '{sbu}' ({dcu})"] += n
    notes.extend(f"normalised {k}: {n} rows" for k, n in by_mapping.items())

    result = BuildResult(
        rows=[out for _, out in canonical],
        errors=errors,
        warnings=warnings,
        notes=notes,
        counts=counts,
        active=active,
    )
    if result.ok:
        result.csv_bytes = to_csv(result.rows)
        # Re-check the finished file exactly as the Phase 3A validator reads it.
        records = master_import.parse_source("canonical.csv", result.csv_bytes)
        master_import.check_file(records)
        result.errors.extend(
            f"Phase 3A file check, row {rec.row} [{rec.alc_code}]: {e}"
            for rec in records
            for e in rec.errors
        )
        if len(records) != len(rows):
            result.errors.append(f"wrote {len(records)} rows but read {len(rows)}")
        if not result.ok:
            result.csv_bytes = b""
    return result


def to_csv(rows: list[dict]) -> bytes:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=master_import.CANONICAL_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def print_report(result: BuildResult) -> None:
    print("Canonical ALC counts (DCU / SBU: built vs confirmed):")
    grand = 0
    for dcu, sbus in result.counts.items():
        total = sum(sbus.values())
        grand += total
        expected_total = sum(EXPECTED_ALC_COUNTS[dcu].values())
        print(f"  {dcu} ({DCU_CODES[dcu]}): {total} (confirmed {expected_total})")
        for sbu, n in sbus.items():
            print(f"    {sbu}: {n} (confirmed {EXPECTED_ALC_COUNTS[dcu][sbu]})")
    print(f"  TOTAL: {grand}")
    for note in result.notes:
        print(f"NOTE: {note}")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    for error in result.errors:
        print(f"ERROR: {error}")


def run(args) -> int:
    try:
        rows = [row for path in args.sources for row in read_source(path)]
        if args.include_nashik:
            rows += read_nashik(args.include_nashik)
    except SourceError as exc:
        print(f"Cannot read source: {exc}")
        return 2
    result = build(
        rows,
        args.default_active,
        include_nashik=bool(args.include_nashik),
        allow_count_mismatch=args.allow_count_mismatch,
    )
    print_report(result)
    if not result.ok:
        print(f"NOT WRITTEN: {len(result.errors)} error(s) remain.")
        return 1
    if args.out is None:
        print(f"Valid: {len(result.rows)} canonical rows (no --out given, nothing written).")
        return 0
    args.out.write_bytes(result.csv_bytes)
    print(f"Wrote {len(result.rows)} canonical rows to {args.out}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the canonical RCU Pune ALC master")
    parser.add_argument("sources", nargs="+", type=Path, help="Source XLSX/CSV files")
    parser.add_argument(
        "--default-active",
        required=True,
        type=str.lower,
        choices=sorted(ACTIVE_CHOICES),
        help="Active value for every row (the sources have no active column).",
    )
    parser.add_argument("--out", type=Path, help="Canonical CSV to write (only if valid).")
    parser.add_argument(
        "--include-nashik",
        type=Path,
        metavar="ALC-MASTER.csv",
        help="Also add the already-imported Nashik list to build the complete master.",
    )
    parser.add_argument(
        "--allow-count-mismatch",
        action="store_true",
        help="Report per-SBU counts that differ from the confirmed counts as warnings.",
    )
    raise SystemExit(run(parser.parse_args()))


if __name__ == "__main__":
    main()
