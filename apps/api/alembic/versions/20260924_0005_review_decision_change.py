"""record reviewer role and decision changes on activity reviews

Revision ID: 20260924_0005
Revises: 20260923_0004
Create Date: 2026-09-24

Additive migration only; no row is deleted and no review is rewritten.

* ``activity_reviews.reviewer_role`` (nullable) — the reviewer's role at decision time, so
  the history can read "SBU verified → DCU changed decision → …" even after a login is
  deleted (``reviewer_id`` is ``ON DELETE SET NULL``).
* ``activity_reviews.is_decision_change`` (not null, default false) — marks a review that
  overrode an earlier final decision (VERIFIED / REJECTED).

Existing reviews are backfilled with their reviewer's current role where the reviewer still
exists; every existing review is a normal decision, so ``is_decision_change`` is false.
Guarded by inspector checks because the initial revision builds a brand-new database from
the current models, which already include these columns.
"""
import sqlalchemy as sa

from alembic import op

revision = "20260924_0005"
down_revision = "20260923_0004"
branch_labels = None
depends_on = None


def _columns(inspector) -> set[str]:
    return {col["name"] for col in inspector.get_columns("activity_reviews")}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(sa.inspect(bind))
    if "reviewer_role" not in columns:
        op.add_column(
            "activity_reviews", sa.Column("reviewer_role", sa.String(length=20), nullable=True)
        )
    if "is_decision_change" not in columns:
        op.add_column(
            "activity_reviews",
            sa.Column(
                "is_decision_change", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
        )
    # Portable correlated backfill (PostgreSQL and SQLite).
    bind.execute(
        sa.text(
            "UPDATE activity_reviews SET reviewer_role = "
            "(SELECT users.role FROM users WHERE users.id = activity_reviews.reviewer_id) "
            "WHERE reviewer_role IS NULL AND reviewer_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    columns = _columns(sa.inspect(op.get_bind()))
    if "is_decision_change" in columns:
        op.drop_column("activity_reviews", "is_decision_change")
    if "reviewer_role" in columns:
        op.drop_column("activity_reviews", "reviewer_role")
