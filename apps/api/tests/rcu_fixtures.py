"""Synthetic RCU Pune source spreadsheets for tests (no real master data required).

The real ``data/Ahilya_Nagar.xlsx`` / ``data/Pune.xlsx`` / ``data/RCU-PUNE-NEW-ALCS.csv`` are
git-ignored local files, so tests build look-alike sources in a temp directory instead:

* the same file split, column header and cell types as the real sources: ``ALC Code`` as a
  numeric (float) "General" cell, ``ALC Name`` / ``DCU`` / ``SBU`` as text;
* the same raw spellings the builder must normalise: DCU ``Ahilyanagar``, SBU
  ``Ahilyanaga_sbu10``, and Pune South coordinator names in the SBU column;
* the confirmed per-SBU ALC counts (203 + 263 + 119 = 585), hard-coded here independently of
  ``app.services.master_mappings`` so the builder's confirmed-count check runs at full size;
* SBUs interleaved within each file, as in the real sources;
* synthetic 8-digit codes with ``9x71`` prefixes, which never collide with the committed Nashik
  master (all ``5721xxxx``), and names that include commas to exercise CSV quoting.
"""

from __future__ import annotations

from pathlib import Path

import openpyxl

# file name -> [(source DCU, source SBU, canonical DCU, canonical SBU, confirmed count)]
SOURCE_LAYOUT = {
    "Ahilya_Nagar.xlsx": [
        ("Ahilyanagar", "Ahilyanagar_sbu1", "Ahilya Nagar", "Ahilyanagar_sbu1", 58),
        ("Ahilyanagar", "Ahilyanagar_sbu2", "Ahilya Nagar", "Ahilyanagar_sbu2", 49),
        ("Ahilyanagar", "Ahilyanagar_sbu5", "Ahilya Nagar", "Ahilyanagar_sbu5", 51),
        ("Ahilyanagar", "Ahilyanaga_sbu10", "Ahilya Nagar", "Ahilyanagar_sbu10", 45),
    ],
    "Pune.xlsx": [
        ("Pune North", "SBU_Pune_North_1", "Pune North", "SBU_Pune_North_1", 53),
        ("Pune North", "SBU_Pune_North_2", "Pune North", "SBU_Pune_North_2", 48),
        ("Pune North", "SBU_Pune_North_3", "Pune North", "SBU_Pune_North_3", 55),
        ("Pune North", "SBU_Pune_North_4", "Pune North", "SBU_Pune_North_4", 52),
        ("Pune North", "SBU_Pune_North_5", "Pune North", "SBU_Pune_North_5", 55),
        ("Pune South", "Bhagyashree Gaikwad", "Pune South", "pune_south_sbu_4", 46),
        ("Pune South", "Ajinkya Chavan", "Pune South", "pune_south_sbu_2", 42),
        ("Pune South", "Aniket Marne", "Pune South", "pune_south_sbu_3", 31),
    ],
}
CODE_PREFIX = {"Ahilya Nagar": 91710000, "Pune North": 92710000, "Pune South": 93710000}
HEADER = ("ALC Code", "ALC Name", "DCU", "SBU")


def source_rows(file_name: str) -> list[dict]:
    """The rows of one synthetic source file, in file order (SBUs interleaved)."""
    queues = []
    for source_dcu, source_sbu, dcu, sbu, count in SOURCE_LAYOUT[file_name]:
        queues.append([(source_dcu, source_sbu, dcu, sbu, i) for i in range(count)])
    rows, serial = [], {}
    while any(queues):
        for queue in queues:
            if not queue:
                continue
            source_dcu, source_sbu, dcu, sbu, i = queue.pop(0)
            serial[dcu] = serial.get(dcu, 0) + 1
            code = CODE_PREFIX[dcu] + serial[dcu]
            name = f"{sbu} Test Centre {i + 1:03d}"
            if i % 10 == 0:
                name += ", Test Town"  # commas must survive CSV quoting untouched
            rows.append(
                {
                    "code": str(code),
                    "name": name,
                    "source_dcu": source_dcu,
                    "source_sbu": source_sbu,
                    "dcu": dcu,
                    "sbu": sbu,
                }
            )
    return rows


def write_source_workbooks(directory: Path) -> list[Path]:
    """Write ``Ahilya_Nagar.xlsx`` and ``Pune.xlsx`` into ``directory`` and return them."""
    paths = []
    for file_name in SOURCE_LAYOUT:
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(list(HEADER))
        for row in source_rows(file_name):
            # Numeric "General" cells, exactly how the real sources store ALC Codes.
            sheet.append([float(row["code"]), row["name"], row["source_dcu"], row["source_sbu"]])
        path = directory / file_name
        workbook.save(path)
        paths.append(path)
    return paths


def build_canonical_master(directory: Path) -> Path:
    """Build ``RCU-PUNE-NEW-ALCS.csv`` from fresh synthetic sources with the real builder."""
    from scripts import build_rcu_master as builder

    sources = write_source_workbooks(directory)
    result = builder.build([r for p in sources for r in builder.read_source(p)], "yes")
    assert result.ok, result.errors[:5]
    out = directory / "RCU-PUNE-NEW-ALCS.csv"
    out.write_bytes(result.csv_bytes)
    return out
