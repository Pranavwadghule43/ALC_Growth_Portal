"""add SBU entity, role support and ALC/User sbu relationships

Revision ID: 20260918_0002
Revises: 20260916_0001
Create Date: 2026-09-18

Additive migration only. It creates the ``sbus`` table and adds nullable
``sbu_id`` foreign keys to ``alcs`` and ``users``. No existing table is
recreated, no data is deleted, and no password is touched. The ``role``
column is a non-native string enum, so the new ``SBU`` value needs no DDL.

Each step is guarded by an inspector check so the migration is idempotent:
the existing initial revision builds the schema with ``metadata.create_all``,
which on a brand-new database already materialises these objects from the
current models, while an established database (upgraded before SBU existed)
still needs them added here.
"""
import sqlalchemy as sa

from alembic import op

revision = "20260918_0002"
down_revision = "20260916_0001"
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, column: str) -> bool:
    return column in {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "sbus" not in tables:
        op.create_table(
            "sbus",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("code", sa.String(length=32), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_sbus")),
            sa.UniqueConstraint("code", name=op.f("uq_sbus_code")),
        )
        op.create_index(op.f("ix_sbus_code"), "sbus", ["code"], unique=True)
        op.create_index(op.f("ix_sbus_is_active"), "sbus", ["is_active"])

    if not _has_column(inspector, "alcs", "sbu_id"):
        op.add_column("alcs", sa.Column("sbu_id", sa.Uuid(), nullable=True))
        op.create_index(op.f("ix_alcs_sbu_id"), "alcs", ["sbu_id"])
        op.create_foreign_key(
            op.f("fk_alcs_sbu_id_sbus"), "alcs", "sbus", ["sbu_id"], ["id"]
        )

    if not _has_column(inspector, "users", "sbu_id"):
        op.add_column("users", sa.Column("sbu_id", sa.Uuid(), nullable=True))
        op.create_index(op.f("ix_users_sbu_id"), "users", ["sbu_id"])
        op.create_foreign_key(
            op.f("fk_users_sbu_id_sbus"), "users", "sbus", ["sbu_id"], ["id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, "users", "sbu_id"):
        op.drop_constraint(op.f("fk_users_sbu_id_sbus"), "users", type_="foreignkey")
        op.drop_index(op.f("ix_users_sbu_id"), table_name="users")
        op.drop_column("users", "sbu_id")

    if _has_column(inspector, "alcs", "sbu_id"):
        op.drop_constraint(op.f("fk_alcs_sbu_id_sbus"), "alcs", type_="foreignkey")
        op.drop_index(op.f("ix_alcs_sbu_id"), table_name="alcs")
        op.drop_column("alcs", "sbu_id")

    if "sbus" in set(inspector.get_table_names()):
        op.drop_index(op.f("ix_sbus_is_active"), table_name="sbus")
        op.drop_index(op.f("ix_sbus_code"), table_name="sbus")
        op.drop_table("sbus")
