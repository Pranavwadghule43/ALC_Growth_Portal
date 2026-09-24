"""Safe bulk provisioning of missing ALC and SBU login accounts.

Follows the project's existing account conventions exactly (see ``routes/auth.py``,
``routes/admin.py::create_user`` and ``scripts/create_alc_users.py``):

* ALC account: ``role=ALC``, ``alc_id`` set, ``sbu_id``/``dcu_id`` NULL. It logs in with its
  **ALC Code** (the login query joins ``User.alc_id -> ALC.alc_code``); the stored username
  follows the existing ``alc-{ALC Code}`` convention. SBU/DCU reach is derived from the live
  hierarchy by ``services.scope``, never stored on the ALC account.
* SBU account: ``role=SBU``, ``sbu_id`` set, ``alc_id``/``dcu_id`` NULL. It logs in with its
  username (or email): here the SBU code lower-cased with every run of non-alphanumerics
  replaced by ``-`` (``SBU 4`` -> ``sbu-4``, ``SBU_Pune_North_1`` -> ``sbu-pune-north-1``).
* Password hashed with ``app.auth.hash_password``; ``must_change_password=True``.

ADMIN and DCU accounts are never created. Existing users are never modified, re-linked,
re-passworded or deleted, and no RCU/DCU/SBU/ALC record is created or changed.

``plan`` classifies every master record without writing anything:

* ``existing``  - the record already has its account (left untouched);
* ``create``    - eligible and missing an account;
* ``inactive``  - the ALC, its SBU, DCU or RCU is inactive (no login access is created);
* ``invalid``   - the ALC -> SBU -> DCU -> RCU chain is broken;
* ``conflict``  - an existing user or login identifier clashes, an assignment is duplicated,
  or a login would be ambiguous.

``provision`` applies a plan inside the caller's transaction (the caller commits) and
returns the one-time plaintext temporary passwords, which are never stored or audited.
"""

from __future__ import annotations

import re
import secrets
import string
import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.enums import AlcStatus, Role
from app.models import ALC, DCU, RCU, SBU, User
from app.services.audit import record_audit

ROLES = {"alc": Role.ALC, "sbu": Role.SBU}
PROVISIONING_METHOD = "bulk_provisioning"

# Temporary passwords: 16 characters, at least one upper, lower, digit and symbol; no
# look-alike characters, and symbols that are safe in CSV / spreadsheets and when read aloud.
PASSWORD_LENGTH = 16
_UPPER = "".join(c for c in string.ascii_uppercase if c not in "IO")
_LOWER = "".join(c for c in string.ascii_lowercase if c not in "lo")
_DIGITS = "23456789"
_SYMBOLS = "!#%*?"
_ALPHABET = _UPPER + _LOWER + _DIGITS + _SYMBOLS


class ProvisioningBlocked(Exception):
    """Provisioning refused before writing anything; carries the plan."""

    def __init__(self, report: dict, reason: str):
        self.report = report
        self.reason = reason
        super().__init__(reason)


@dataclass
class Credential:
    """One newly created account and its one-time temporary password (never persisted)."""

    login_identifier: str
    username: str
    display_name: str
    role: str
    rcu: str
    dcu: str
    sbu: str
    alc_code: str
    temporary_password: str


def generate_password() -> str:
    while True:
        password = secrets.choice(_UPPER + _LOWER) + "".join(
            secrets.choice(_ALPHABET) for _ in range(PASSWORD_LENGTH - 1)
        )
        if (
            any(c in _UPPER for c in password)
            and any(c in _LOWER for c in password)
            and any(c in _DIGITS for c in password)
            and any(c in _SYMBOLS for c in password)
        ):
            return password


def alc_username(alc_code: str) -> str:
    return f"alc-{alc_code}"


def sbu_username(sbu_code: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", sbu_code.lower()).strip("-")


# --------------------------------------------------------------------------- #
# Planning (never writes)
# --------------------------------------------------------------------------- #
@dataclass
class _World:
    rcus: dict
    dcus: dict
    sbus: dict
    alcs: list
    users: list
    by_username: dict
    by_email: dict
    alc_codes: dict


async def _load(db: AsyncSession) -> _World:
    rcus = {r.id: r for r in (await db.scalars(select(RCU))).all()}
    dcus = {d.id: d for d in (await db.scalars(select(DCU))).all()}
    sbus = {s.id: s for s in (await db.scalars(select(SBU))).all()}
    alcs = (await db.scalars(select(ALC).order_by(ALC.alc_code))).all()
    users = (await db.scalars(select(User).order_by(User.username))).all()
    by_username, by_email, alc_codes = defaultdict(list), defaultdict(list), defaultdict(list)
    for u in users:
        by_username[u.username.lower()].append(u)
        if u.email:
            by_email[u.email.lower()].append(u)
    for a in alcs:
        alc_codes[a.alc_code.lower()].append(a)
    return _World(rcus, dcus, sbus, alcs, users, by_username, by_email, alc_codes)


def _describe(user: User) -> str:
    return f"'{user.username}' ({user.role.value})"


def _chain(world: _World, sbu_id) -> tuple[SBU | None, DCU | None, RCU | None, str | None]:
    """Resolve SBU -> DCU -> RCU; the last element is the reason the chain is broken."""
    if sbu_id is None:
        return None, None, None, "ALC has no SBU"
    sbu = world.sbus.get(sbu_id)
    if sbu is None:
        return None, None, None, "SBU record missing"
    if sbu.dcu_id is None:
        return sbu, None, None, f"SBU '{sbu.code}' has no DCU"
    dcu = world.dcus.get(sbu.dcu_id)
    if dcu is None:
        return sbu, None, None, f"DCU of SBU '{sbu.code}' is missing"
    rcu = world.rcus.get(dcu.rcu_id)
    if rcu is None:
        return sbu, dcu, None, f"RCU of DCU '{dcu.code}' is missing"
    return sbu, dcu, rcu, None


def _inactive_reason(alc: ALC | None, sbu: SBU, dcu: DCU, rcu: RCU) -> str | None:
    if alc is not None and alc.status != AlcStatus.ACTIVE:
        return f"ALC is {alc.status.value}"
    if not sbu.is_active:
        return f"SBU '{sbu.code}' is inactive"
    if not dcu.is_active:
        return f"DCU '{dcu.code}' is inactive"
    if not rcu.is_active:
        return f"RCU '{rcu.code}' is inactive"
    return None


_PORTAL_NAMED_ROLES = (Role.DCU, Role.SBU)  # portal login matches these by username/email


def _login_clashes(world: _World, identifier: str, *, owner: object) -> list[str]:
    """Everything else the portal login (``/auth/login``) would also match for this
    identifier: DCU/SBU usernames or emails, and ALC Codes (all case-insensitive)."""
    key = identifier.lower()
    clashes = [
        f"username of {_describe(u)}"
        for u in world.by_username.get(key, [])
        if u.role in _PORTAL_NAMED_ROLES
    ]
    clashes += [
        f"email of {_describe(u)}"
        for u in world.by_email.get(key, [])
        if u.role in _PORTAL_NAMED_ROLES
    ]
    clashes += [f"ALC Code '{a.alc_code}'" for a in world.alc_codes.get(key, []) if a is not owner]
    return clashes


def _item(action: str, **fields) -> dict:
    return {"action": action, **fields}


def _plan_alcs(world: _World) -> list[dict]:
    linked = defaultdict(list)
    for u in world.users:
        if u.alc_id is not None:
            linked[u.alc_id].append(u)
    items = []
    for alc in world.alcs:
        sbu, dcu, rcu, broken = _chain(world, alc.sbu_id)
        base = {
            "entity_id": str(alc.id),
            "alc_code": alc.alc_code,
            "display_name": alc.alc_name,
            "login_identifier": alc.alc_code,
            "username": alc_username(alc.alc_code),
            "rcu": rcu.name if rcu else "",
            "dcu": dcu.name if dcu else "",
            "sbu": sbu.code if sbu else "",
            "_alc": alc,
        }
        users = linked.get(alc.id, [])
        wrong_role = [u for u in users if u.role != Role.ALC]
        alc_users = [u for u in users if u.role == Role.ALC]
        if wrong_role:
            items.append(
                _item(
                    "conflict",
                    **base,
                    reason="linked to non-ALC user(s) "
                    + ", ".join(_describe(u) for u in wrong_role),
                )
            )
        elif len(alc_users) > 1:
            items.append(
                _item(
                    "conflict",
                    **base,
                    reason="duplicate assignment: " + ", ".join(_describe(u) for u in alc_users),
                )
            )
        elif alc_users:
            user = alc_users[0]
            if user.sbu_id is not None or user.dcu_id is not None:
                items.append(
                    _item(
                        "conflict",
                        **base,
                        reason=f"existing ALC account "
                        f"{_describe(user)} also carries an SBU/DCU key",
                    )
                )
            else:
                note = None if user.username == base["username"] else "non-standard username"
                items.append(_item("existing", **base, existing_username=user.username, note=note))
        elif broken:
            items.append(_item("invalid", **base, reason=broken))
        elif reason := _inactive_reason(alc, sbu, dcu, rcu):
            items.append(_item("inactive", **base, reason=reason))
        elif world.by_username.get(base["username"].lower()):
            owner = world.by_username[base["username"].lower()][0]
            items.append(
                _item(
                    "conflict",
                    **base,
                    reason=f"username '{base['username']}' already belongs to {_describe(owner)}",
                )
            )
        elif clashes := _login_clashes(world, alc.alc_code, owner=alc):
            items.append(
                _item(
                    "conflict",
                    **base,
                    reason="ambiguous login: ALC Code also matches " + ", ".join(clashes),
                )
            )
        else:
            items.append(_item("create", **base))
    return items


def _plan_sbus(world: _World) -> list[dict]:
    linked = defaultdict(list)
    for u in world.users:
        if u.sbu_id is not None:
            linked[u.sbu_id].append(u)
    items = []
    for sbu in sorted(world.sbus.values(), key=lambda s: s.code):
        _, dcu, rcu, broken = _chain(world, sbu.id)
        username = sbu_username(sbu.code)
        base = {
            "entity_id": str(sbu.id),
            "alc_code": "",
            "display_name": sbu.name,
            "login_identifier": username,
            "username": username,
            "rcu": rcu.name if rcu else "",
            "dcu": dcu.name if dcu else "",
            "sbu": sbu.code,
            "_sbu": sbu,
        }
        users = linked.get(sbu.id, [])
        wrong_role = [u for u in users if u.role != Role.SBU]
        sbu_users = [u for u in users if u.role == Role.SBU]
        bad_keys = [u for u in sbu_users if u.alc_id is not None or u.dcu_id is not None]
        if wrong_role:
            items.append(
                _item(
                    "conflict",
                    **base,
                    reason="linked to non-SBU user(s) "
                    + ", ".join(_describe(u) for u in wrong_role),
                )
            )
        elif bad_keys:
            items.append(
                _item(
                    "conflict",
                    **base,
                    reason="existing SBU account(s) also carry "
                    "an ALC/DCU key: " + ", ".join(_describe(u) for u in bad_keys),
                )
            )
        elif sbu_users:
            items.append(
                _item(
                    "existing",
                    **base,
                    existing_username=", ".join(u.username for u in sbu_users),
                    note=None if len(sbu_users) == 1 else f"{len(sbu_users)} SBU accounts",
                )
            )
        elif broken:
            items.append(_item("invalid", **base, reason=broken))
        elif reason := _inactive_reason(None, sbu, dcu, rcu):
            items.append(_item("inactive", **base, reason=reason))
        elif not username:
            items.append(_item("invalid", **base, reason="SBU code gives an empty username"))
        elif world.by_username.get(username):
            owner = world.by_username[username][0]
            items.append(
                _item(
                    "conflict",
                    **base,
                    reason=f"username '{username}' already belongs to {_describe(owner)}",
                )
            )
        elif clashes := _login_clashes(world, username, owner=None):
            items.append(
                _item(
                    "conflict",
                    **base,
                    reason=f"login '{username}' already matches " + ", ".join(clashes),
                )
            )
        else:
            items.append(_item("create", **base))

    # Two SBUs whose codes produce the same username.
    creates = defaultdict(list)
    for item in items:
        if item["action"] == "create":
            creates[item["username"]].append(item)
    for username, group in creates.items():
        if len(group) > 1:
            for item in group:
                item["action"] = "conflict"
                item["reason"] = f"username '{username}' would be shared by SBUs " + ", ".join(
                    i["sbu"] for i in group
                )
    return items


def _counts(items: list[dict]) -> dict[str, int]:
    counts = {
        "total": len(items),
        "eligible": 0,
        "create": 0,
        "existing": 0,
        "inactive": 0,
        "conflict": 0,
        "invalid": 0,
    }
    for item in items:
        counts[item["action"]] += 1
    counts["eligible"] = counts["create"] + counts["existing"]
    return counts


async def plan(db: AsyncSession, role: str) -> dict:
    """Classify every ALC (``role='alc'``) or SBU (``role='sbu'``) master record."""
    if role not in ROLES:
        raise ValueError("Only ALC and SBU accounts can be provisioned")
    world = await _load(db)
    items = _plan_alcs(world) if role == "alc" else _plan_sbus(world)
    return {"role": ROLES[role].value, "items": items, "counts": _counts(items)}


def public(report: dict) -> dict:
    """The plan without internal ORM references (safe to print or serialise)."""
    items = [{k: v for k, v in i.items() if not k.startswith("_")} for i in report["items"]]
    return {**report, "items": items}


# --------------------------------------------------------------------------- #
# Provisioning (in the caller's transaction)
# --------------------------------------------------------------------------- #
async def provision(
    db: AsyncSession,
    role: str,
    *,
    allow_partial: bool = False,
    expect_create: int | None = None,
    password_factory=generate_password,
) -> tuple[dict, list[Credential]]:
    """Create the planned accounts and return ``(plan, credentials)``. Refuses, writing
    nothing, when the plan has conflicts or invalid hierarchy (unless ``allow_partial``) or
    when ``expect_create`` does not match the planned count. The caller commits."""
    report = await plan(db, role)
    counts = report["counts"]
    if (counts["conflict"] or counts["invalid"]) and not allow_partial:
        raise ProvisioningBlocked(
            report,
            f"{counts['conflict']} conflict(s) and {counts['invalid']} invalid hierarchy "
            "record(s) must be resolved first (or pass allow_partial to skip them)",
        )
    if expect_create is not None and expect_create != counts["create"]:
        raise ProvisioningBlocked(
            report,
            f"expected to create {expect_create} account(s) but the plan creates "
            f"{counts['create']}; re-run the dry run and review",
        )

    batch = str(uuid.uuid4())
    credentials: list[Credential] = []
    for item in report["items"]:
        if item["action"] != "create":
            continue
        password = password_factory()
        user = User(
            username=item["username"],
            email=None,
            role=ROLES[role],
            alc_id=item["_alc"].id if role == "alc" else None,
            sbu_id=item["_sbu"].id if role == "sbu" else None,
            dcu_id=None,
            password_hash=hash_password(password),
            is_active=True,
            must_change_password=True,
        )
        db.add(user)
        await db.flush()
        # Audit the account, never the password or its hash.
        await record_audit(
            db,
            "user_created",
            "user",
            user.id,
            metadata={
                "method": PROVISIONING_METHOD,
                "batch": batch,
                "role": ROLES[role].value,
                "username": user.username,
                "alc_code": item["alc_code"] or None,
                "sbu": item["sbu"],
            },
        )
        credentials.append(
            Credential(
                login_identifier=item["login_identifier"],
                username=item["username"],
                display_name=item["display_name"],
                role=ROLES[role].value,
                rcu=item["rcu"],
                dcu=item["dcu"],
                sbu=item["sbu"],
                alc_code=item["alc_code"],
                temporary_password=password,
            )
        )
    if credentials:
        await record_audit(
            db,
            "accounts_provisioned",
            "user",
            metadata={
                "method": PROVISIONING_METHOD,
                "batch": batch,
                "role": ROLES[role].value,
                "created": len(credentials),
                "existing": counts["existing"],
                "inactive": counts["inactive"],
                "conflict": counts["conflict"],
                "invalid": counts["invalid"],
            },
        )
    await db.flush()
    report["batch"] = batch
    return report, credentials
