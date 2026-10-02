"""global Growth Challenge configuration

Revision ID: 20261002_0007
Revises: 20260930_0006
Create Date: 2026-10-02

Adds the ``growth_challenges`` table: one row per configured Growth Challenge period (name,
inclusive start / end dates of any length, the four targets, who created / last updated it).
A configured challenge applies to every ALC, including ALCs created later, so no per-ALC
rows are written.

Additive only. No existing table is altered and no row is inserted, updated or deleted, so
there is nothing to backfill: ``challenge_progress`` (per-ALC overrides) keeps all its data,
and a database with no challenge configured reports the challenge as NOT_CONFIGURED.

Constraints:

* ``ck_growth_challenges_period_order``: ``end_date >= start_date``.
* ``ck_growth_challenges_targets_non_negative``: no negative target.
* ``ex_growth_challenges_no_overlap`` (PostgreSQL only): two challenges may not share a day,
  so a concurrent double-submit cannot slip past the API's overlap check.

Idempotent, like ``20260918_0002`` / ``20260923_0004``: the initial revision builds a brand-new
database from the current models (which already declare this table), so an existing table is
left alone and only what is missing is created.

Downgrade drops the table (and with it the configured challenge periods); nothing else is
touched.
"""
import sqlalchemy as sa

from alembic import op

revision = "20261002_0007"
down_revision = "20260930_0006"
branch_labels = None
depends_on = None

TABLE = "growth_challenges"
NO_OVERLAP = "ex_growth_challenges_no_overlap"


def _postgres_has_constraint(bind, name: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE c.conname = :name AND t.relname = :table AND n.nspname = current_schema()"
            ),
            {"name": name, "table": TABLE},
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()
    if TABLE not in set(sa.inspect(bind).get_table_names()):
        op.create_table(
            TABLE,
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=150), nullable=False),
            sa.Column("start_date", sa.Date(), nullable=False),
            sa.Column("end_date", sa.Date(), nullable=False),
            sa.Column("prospects_target", sa.Integer(), nullable=False),
            sa.Column("meetings_target", sa.Integer(), nullable=False),
            sa.Column("pilots_target", sa.Integer(), nullable=False),
            sa.Column("partnerships_target", sa.Integer(), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_growth_challenges")),
            sa.CheckConstraint(
                "end_date >= start_date", name=op.f("ck_growth_challenges_period_order")
            ),
            sa.CheckConstraint(
                "prospects_target >= 0 AND meetings_target >= 0 "
                "AND pilots_target >= 0 AND partnerships_target >= 0",
                name=op.f("ck_growth_challenges_targets_non_negative"),
            ),
            sa.ForeignKeyConstraint(
                ["created_by"], ["users.id"], ondelete="SET NULL",
                name=op.f("fk_growth_challenges_created_by_users"),
            ),
            sa.ForeignKeyConstraint(
                ["updated_by"], ["users.id"], ondelete="SET NULL",
                name=op.f("fk_growth_challenges_updated_by_users"),
            ),
        )
        op.create_index(op.f("ix_growth_challenges_start_date"), TABLE, ["start_date"])
        op.create_index(op.f("ix_growth_challenges_end_date"), TABLE, ["end_date"])
    if bind.dialect.name == "postgresql" and not _postgres_has_constraint(bind, NO_OVERLAP):
        # '[]' = both dates inclusive; && = the two periods share at least one day.
        op.execute(
            sa.text(
                f"ALTER TABLE {TABLE} ADD CONSTRAINT {NO_OVERLAP} "
                "EXCLUDE USING gist (daterange(start_date, end_date, '[]') WITH &&)"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if TABLE in set(sa.inspect(bind).get_table_names()):
        op.drop_table(TABLE)  # its indexes and constraints go with it
