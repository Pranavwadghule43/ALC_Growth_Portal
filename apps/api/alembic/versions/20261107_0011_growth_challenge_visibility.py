"""Add explicit Growth Challenge publication and description.

Revision ID: 20261107_0011
Revises: 20261104_0010
"""
import sqlalchemy as sa

from alembic import op

revision = "20261107_0011"
down_revision = "20261104_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("growth_challenges"):
        raise RuntimeError("growth_challenges table is required before migration 0011")

    columns = {
        column["name"]
        for column in inspector.get_columns("growth_challenges")
    }

    if "description" not in columns:
        op.add_column(
            "growth_challenges",
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
        )

    if "is_published" not in columns:
        op.add_column(
            "growth_challenges",
            sa.Column(
                "is_published",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )

    indexes = {
        index["name"]
        for index in sa.inspect(bind).get_indexes("growth_challenges")
    }

    if op.f("ix_growth_challenges_is_published") not in indexes:
        op.create_index(
            op.f("ix_growth_challenges_is_published"),
            "growth_challenges",
            ["is_published"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("growth_challenges"):
        raise RuntimeError("growth_challenges table is required before migration 0011")
    columns = {column["name"] for column in inspector.get_columns("growth_challenges")}
    indexes = {index["name"] for index in inspector.get_indexes("growth_challenges")}
    publication_index = op.f("ix_growth_challenges_is_published")
    if publication_index in indexes:
        op.drop_index(publication_index, table_name="growth_challenges")
    if "is_published" in columns:
        op.drop_column("growth_challenges", "is_published")
    if "description" in columns:
        op.drop_column("growth_challenges", "description")
