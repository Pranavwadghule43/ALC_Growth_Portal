"""Hierarchy-aware data scope: the single source of truth for which ALCs a user may reach.

The organisational hierarchy is ``RCU → DCU → SBU → ALC``. Access is always resolved
server-side from the authenticated user's role and relationship key, walking the *current*
hierarchy on every request, so reassigning an ALC to another SBU (or an SBU to another DCU)
changes access immediately without touching any activity, partner, evidence or review row:

* ``ADMIN`` → every ALC.
* ``DCU``   → ALCs whose SBU belongs to ``user.dcu_id`` (``ALC.sbu_id → SBU.dcu_id``).
* ``SBU``   → ALCs where ``ALC.sbu_id == user.sbu_id``.
* ``ALC``   → only ``user.alc_id``.

Every rule fails closed: a missing relationship key (``dcu_id`` / ``sbu_id`` / ``alc_id`` is
NULL) or an unknown role yields an always-false clause, never an ``IS NULL`` match and never
"see everything". Client-supplied ``dcu_id`` / ``sbu_id`` / ``alc_id`` values are only ever
used to *narrow* this scope, never as proof of authorization.

The scope subqueries select from aliases with correlation disabled, so a clause built here can
be embedded in a query that itself selects from ``alcs`` / ``sbus`` without SQLAlchemy
auto-correlating the subquery away.
"""
import uuid
from collections.abc import Sequence

from sqlalchemy import ColumnElement, false, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, aliased

from app.enums import Role
from app.models import ALC, SBU, User

# Roles that supervise many ALCs through the portal (review queue, ALC directory, reports).
SUPERVISOR_ROLES = frozenset({Role.DCU, Role.SBU})


def has_scope_key(user: User) -> bool:
    """True when an operational user carries the relationship key its role requires."""
    if user.role == Role.DCU:
        return user.dcu_id is not None
    if user.role == Role.SBU:
        return user.sbu_id is not None
    if user.role == Role.ALC:
        return user.alc_id is not None
    return False


def alc_scope(user: User, alc_column: InstrumentedAttribute = ALC.id) -> ColumnElement[bool]:
    """Boolean clause restricting ``alc_column`` (any column holding an ALC id, e.g.
    ``Activity.alc_id`` or ``Partner.alc_id``) to the ALCs ``user`` may access."""
    if user.role == Role.ADMIN:
        return true()
    if user.role == Role.DCU:
        if user.dcu_id is None:
            return false()
        alc, sbu = aliased(ALC), aliased(SBU)
        return alc_column.in_(
            select(alc.id)
            .join(sbu, alc.sbu_id == sbu.id)
            .where(sbu.dcu_id == user.dcu_id)
            .correlate(None)
        )
    if user.role == Role.SBU:
        if user.sbu_id is None:
            return false()
        alc = aliased(ALC)
        return alc_column.in_(select(alc.id).where(alc.sbu_id == user.sbu_id).correlate(None))
    if user.role == Role.ALC:
        if user.alc_id is None:
            return false()
        return alc_column == user.alc_id
    return false()


def sbu_scope(user: User, sbu_column: InstrumentedAttribute = SBU.id) -> ColumnElement[bool]:
    """Boolean clause restricting ``sbu_column`` to the SBUs ``user`` may access.

    ALC users do not browse SBUs, so they get no SBU scope."""
    if user.role == Role.ADMIN:
        return true()
    if user.role == Role.DCU:
        if user.dcu_id is None:
            return false()
        sbu = aliased(SBU)
        return sbu_column.in_(select(sbu.id).where(sbu.dcu_id == user.dcu_id).correlate(None))
    if user.role == Role.SBU:
        if user.sbu_id is None:
            return false()
        return sbu_column == user.sbu_id
    return false()


async def accessible_alc_ids(db: AsyncSession, user: User) -> Sequence[uuid.UUID]:
    """Materialised list of accessible ALC ids (empty when the scope is empty)."""
    return (await db.scalars(select(ALC.id).where(alc_scope(user)))).all()


async def can_access_alc(db: AsyncSession, user: User, alc_id: uuid.UUID) -> bool:
    return (
        await db.scalar(select(ALC.id).where(ALC.id == alc_id, alc_scope(user)))
    ) is not None


async def can_access_sbu(db: AsyncSession, user: User, sbu_id: uuid.UUID) -> bool:
    return (
        await db.scalar(select(SBU.id).where(SBU.id == sbu_id, sbu_scope(user)))
    ) is not None
