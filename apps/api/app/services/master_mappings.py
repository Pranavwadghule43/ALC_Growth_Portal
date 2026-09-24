"""Confirmed RCU Pune hierarchy mappings: the single source of truth for Phase 3B.

Every business mapping used to seed the remaining SBUs and to build the canonical RCU Pune
master lives here — nothing is hard-coded in the scripts. Values on the left of an alias map
are spellings found in the source spreadsheets; values on the right are the canonical
identifiers written to the master and stored as ``SBU.code``.

Source lookups are case-insensitive and ignore surrounding / repeated whitespace only; any
other spelling is reported as unresolved rather than guessed.
"""

from app.services.hierarchy import RCU_PUNE, SBU_DCU_LINKS

RCU_CANONICAL = "RCU Pune"
RCU_CODE = RCU_PUNE[0]

# Canonical DCU name (as written to the master) -> existing DCU.code in the database.
DCU_CODES = {
    "Ahilya Nagar": "DCU_AHILYA_NAGAR",
    "Nashik": "DCU_NASHIK",
    "Pune North": "DCU_PUNE_NORTH",
    "Pune South": "DCU_PUNE_SOUTH",
}

# Alternate DCU spellings seen in the sources -> canonical DCU name.
DCU_ALIASES = {
    "Ahilyanagar": "Ahilya Nagar",
}

# DCUs whose SBUs and ALCs are prepared in Phase 3B. Nashik is already complete.
NEW_DCUS = ("Ahilya Nagar", "Pune North", "Pune South")

# Canonical SBU identifiers per DCU, in display order.
SBUS_BY_DCU = {
    "Ahilya Nagar": (
        "Ahilyanagar_sbu1",
        "Ahilyanagar_sbu2",
        "Ahilyanagar_sbu5",
        "Ahilyanagar_sbu10",
    ),
    "Pune North": (
        "SBU_Pune_North_1",
        "SBU_Pune_North_2",
        "SBU_Pune_North_3",
        "SBU_Pune_North_4",
        "SBU_Pune_North_5",
    ),
    "Pune South": (
        "pune_south_sbu_2",
        "pune_south_sbu_3",
        "pune_south_sbu_4",
    ),
    # Existing and unchanged; seeded by ``hierarchy.ensure_hierarchy``.
    "Nashik": tuple(sorted(s for s, d in SBU_DCU_LINKS.items() if d == "DCU_NASHIK")),
}

# Source SBU spellings that differ from the canonical identifier, per DCU. Every canonical
# identifier also maps to itself, so a canonical master can be fed back through the builder.
SBU_ALIASES = {
    "Ahilya Nagar": {
        "Ahilyanaga_sbu10": "Ahilyanagar_sbu10",  # confirmed spelling correction
    },
    "Pune South": {
        # The source names the SBU coordinator; these are not SBU identifiers.
        "Bhagyashree Gaikwad": "pune_south_sbu_4",
        "Ajinkya Chavan": "pune_south_sbu_2",
        "Aniket Marne": "pune_south_sbu_3",
    },
}

# Friendly display names (stored as ``SBU.name``). ``SBU.code`` stays the canonical identifier;
# names are never used to build or import the master.
SBU_DISPLAY_NAMES = {
    "Ahilyanagar_sbu1": "Ahilya Nagar SBU 1",
    "Ahilyanagar_sbu2": "Ahilya Nagar SBU 2",
    "Ahilyanagar_sbu5": "Ahilya Nagar SBU 5",
    "Ahilyanagar_sbu10": "Ahilya Nagar SBU 10",
    "SBU_Pune_North_1": "Pune North SBU 1",
    "SBU_Pune_North_2": "Pune North SBU 2",
    "SBU_Pune_North_3": "Pune North SBU 3",
    "SBU_Pune_North_4": "Pune North SBU 4",
    "SBU_Pune_North_5": "Pune North SBU 5",
    "pune_south_sbu_2": "Pune South SBU 2 - Ajinkya Chavan",
    "pune_south_sbu_3": "Pune South SBU 3 - Aniket Marne",
    "pune_south_sbu_4": "Pune South SBU 4 - Bhagyashree Gaikwad",
}

# Confirmed ALC counts per canonical SBU. The builder reports any difference.
EXPECTED_ALC_COUNTS = {
    "Ahilya Nagar": {
        "Ahilyanagar_sbu1": 58,
        "Ahilyanagar_sbu2": 49,
        "Ahilyanagar_sbu5": 51,
        "Ahilyanagar_sbu10": 45,
    },
    "Pune North": {
        "SBU_Pune_North_1": 53,
        "SBU_Pune_North_2": 48,
        "SBU_Pune_North_3": 55,
        "SBU_Pune_North_4": 52,
        "SBU_Pune_North_5": 55,
    },
    "Pune South": {
        "pune_south_sbu_4": 46,
        "pune_south_sbu_2": 42,
        "pune_south_sbu_3": 31,
    },
    "Nashik": {"SBU 4": 71, "SBU 6": 58, "SBU 7": 70},
}


def _fold(value: str) -> str:
    return " ".join(str(value).split()).casefold()


_DCU_LOOKUP = {_fold(name): name for name in DCU_CODES} | {
    _fold(alias): name for alias, name in DCU_ALIASES.items()
}
_SBU_LOOKUP = {
    dcu: {_fold(sbu): sbu for sbu in sbus}
    | {_fold(a): c for a, c in SBU_ALIASES.get(dcu, {}).items()}
    for dcu, sbus in SBUS_BY_DCU.items()
}


def canonical_dcu(source: str) -> str | None:
    """Canonical DCU name for a source spelling, or ``None`` if it is not a confirmed one."""
    return _DCU_LOOKUP.get(_fold(source)) if source else None


def canonical_sbu(dcu: str, source: str) -> str | None:
    """Canonical SBU identifier for a source value within a canonical DCU, or ``None``."""
    return _SBU_LOOKUP.get(dcu, {}).get(_fold(source)) if source else None


def remaining_sbus() -> list[tuple[str, str]]:
    """``(canonical SBU identifier, DCU code)`` for every SBU Phase 3B must seed."""
    return [(sbu, DCU_CODES[dcu]) for dcu in NEW_DCUS for sbu in SBUS_BY_DCU[dcu]]
