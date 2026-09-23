"""add RCU → DCU hierarchy above SBU, DCU role support, and hierarchy indexes

Revision ID: 20260923_0004
Revises: 20260920_0003
Create Date: 2026-09-23

Additive migration only; no table is recreated and no row is deleted.

Schema
* creates ``rcus`` and ``dcus`` (``dcus.rcu_id → rcus.id``);
* adds nullable ``sbus.dcu_id → dcus.id`` and ``users.dcu_id → dcus.id`` (indexed).
* ``users.role`` is a non-native string enum (``VARCHAR`` sized to the longest value, 5,
  with no CHECK constraint), so the new ``DCU`` value (3 chars) needs no DDL.

Data (idempotent, matched by stable code)
* RCU ``RCU_PUNE`` and DCUs ``DCU_AHILYA_NAGAR``, ``DCU_NASHIK``, ``DCU_PUNE_NORTH``,
  ``DCU_PUNE_SOUTH``;
* the real Nashik SBUs (SBU 4, SBU 6, SBU 7) are linked to ``DCU_NASHIK`` only if they exist
  and are not already assigned. No other SBU is touched — the demo "SBU 1" stays unassigned so
  no DCU can reach it. ``alcs.sbu_id`` is never modified, so every ALC → SBU mapping is kept.

Indexes
* inspects the hierarchy-filter indexes the scope queries rely on and creates any that are
  missing (a database built by ``20260916_0001`` via ``create_all`` already has them).

Every DDL step is guarded by an inspector check (as in ``20260918_0002``) because the initial
revision builds a brand-new database from the *current* models, which already include these
objects. Constants are frozen here on purpose; ``app/services/hierarchy.py`` holds the same
data for the post-deploy ``scripts/seed_hierarchy.py`` re-run.
"""
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from alembic import op

revision = "20260923_0004"
down_revision = "20260920_0003"
branch_labels = None
depends_on = None

RCU_PUNE = ("RCU_PUNE", "RCU Pune")
DCUS = (
    ("DCU_AHILYA_NAGAR", "DCU Ahilya Nagar"),
    ("DCU_NASHIK", "DCU Nashik"),
    ("DCU_PUNE_NORTH", "DCU Pune North"),
    ("DCU_PUNE_SOUTH", "DCU Pune South"),
)
NASHIK_SBU_CODES = ("sbu 4", "sbu 6", "sbu 7")  # compared against lower(trim(sbus.code))

# (index name, table, columns) the hierarchy scope queries depend on.
HIERARCHY_INDEXES = (
    ("ix_alcs_sbu_id", "alcs", ["sbu_id"]),
    ("ix_users_alc_id", "users", ["alc_id"]),
    ("ix_users_sbu_id", "users", ["sbu_id"]),
    ("ix_activities_alc_status", "activities", ["alc_id", "status"]),
    ("ix_activities_status", "activities", ["status"]),
    ("ix_activities_activity_date", "activities", ["activity_date"]),
    ("ix_activities_submitted", "activities", ["submitted_at"]),
    ("ix_partners_alc_id", "partners", ["alc_id"]),
)


def _has_column(inspector, table: str, column: str) -> bool:
    return column in {col["name"] for col in inspector.get_columns(table)}


def _has_index_on(inspector, table: str, columns: list[str]) -> bool:
    """True if some index (or unique constraint) on ``table`` starts with ``columns``."""
    candidates = [ix["column_names"] for ix in inspector.get_indexes(table)]
    candidates += [uq["column_names"] for uq in inspector.get_unique_constraints(table)]
    return any(list(cols[: len(columns)]) == columns for cols in candidates)


def _add_dcu_fk_column(bind, inspector, table: str) -> None:
    if _has_column(inspector, table, "dcu_id"):
        return
    op.add_column(table, sa.Column("dcu_id", sa.Uuid(), nullable=True))
    op.create_index(op.f(f"ix_{table}_dcu_id"), table, ["dcu_id"])
    if bind.dialect.name != "sqlite":  # SQLite cannot ALTER TABLE ADD CONSTRAINT
        op.create_foreign_key(op.f(f"fk_{table}_dcu_id_dcus"), table, "dcus", ["dcu_id"], ["id"])


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "rcus" not in tables:
        op.create_table(
            "rcus",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("code", sa.String(length=32), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_rcus")),
        )
        op.create_index(op.f("ix_rcus_code"), "rcus", ["code"], unique=True)

    if "dcus" not in tables:
        op.create_table(
            "dcus",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("code", sa.String(length=32), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("rcu_id", sa.Uuid(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["rcu_id"], ["rcus.id"], name=op.f("fk_dcus_rcu_id_rcus")),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_dcus")),
        )
        op.create_index(op.f("ix_dcus_code"), "dcus", ["code"], unique=True)
        op.create_index(op.f("ix_dcus_rcu_id"), "dcus", ["rcu_id"])

    _add_dcu_fk_column(bind, inspector, "sbus")
    _add_dcu_fk_column(bind, inspector, "users")

    inspector = sa.inspect(bind)  # refresh after DDL
    for name, table, columns in HIERARCHY_INDEXES:
        if not _has_index_on(inspector, table, columns):
            op.create_index(name, table, columns)

    _seed_hierarchy(bind)


def _seed_hierarchy(bind) -> None:
    now = datetime.now(timezone.utc)
    rcus = sa.table(
        "rcus",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    dcus = sa.table(
        "dcus",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("rcu_id", sa.Uuid()),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    sbus = sa.table("sbus", sa.column("code", sa.String()), sa.column("dcu_id", sa.Uuid()))

    rcu_code, rcu_name = RCU_PUNE
    rcu_id = bind.scalar(sa.select(rcus.c.id).where(rcus.c.code == rcu_code))
    if rcu_id is None:
        rcu_id = uuid.uuid4()
        bind.execute(
            rcus.insert().values(
                id=rcu_id, code=rcu_code, name=rcu_name, is_active=True,
                created_at=now, updated_at=now,
            )
        )

    for code, name in DCUS:
        if bind.scalar(sa.select(dcus.c.id).where(dcus.c.code == code)) is None:
            bind.execute(
                dcus.insert().values(
                    id=uuid.uuid4(), code=code, name=name, rcu_id=rcu_id, is_active=True,
                    created_at=now, updated_at=now,
                )
            )

    nashik_id = bind.scalar(sa.select(dcus.c.id).where(dcus.c.code == "DCU_NASHIK"))
    bind.execute(
        sbus.update()
        .where(
            sa.func.lower(sa.func.trim(sbus.c.code)).in_(NASHIK_SBU_CODES),
            sbus.c.dcu_id.is_(None),
        )
        .values(dcu_id=nashik_id)
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Refuse rather than silently strand DCU logins that older code cannot load.
    if _has_column(inspector, "users", "dcu_id"):
        dcu_users = bind.scalar(sa.text("SELECT count(*) FROM users WHERE role = 'DCU'"))
        if dcu_users:
            raise RuntimeError(
                f"{dcu_users} DCU user account(s) exist; delete or re-role them before "
                "downgrading below 20260923_0004."
            )

    # Hierarchy indexes created above are left in place: they predate this revision on any
    # database built from the initial schema and dropping them could remove pre-existing ones.
    for table in ("users", "sbus"):
        if _has_column(inspector, table, "dcu_id"):
            if bind.dialect.name != "sqlite":
                op.drop_constraint(op.f(f"fk_{table}_dcu_id_dcus"), table, type_="foreignkey")
            op.drop_index(op.f(f"ix_{table}_dcu_id"), table_name=table)
            op.drop_column(table, "dcu_id")

    tables = set(inspector.get_table_names())
    if "dcus" in tables:
        op.drop_index(op.f("ix_dcus_rcu_id"), table_name="dcus")
        op.drop_index(op.f("ix_dcus_code"), table_name="dcus")
        op.drop_table("dcus")
    if "rcus" in tables:
        op.drop_index(op.f("ix_rcus_code"), table_name="rcus")
        op.drop_table("rcus")
