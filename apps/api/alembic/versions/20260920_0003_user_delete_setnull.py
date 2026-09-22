"""Allow deleting a login account while preserving historical rows.

Makes the user-authorship foreign keys nullable and ``ON DELETE SET NULL`` so an ALC/SBU
login can be permanently deleted without cascading away activities, evidence, reviews,
revisions or audit history — the rows remain, their author reference is cleared.

Idempotent-ish and additive: no data is deleted, no table recreated.
"""
import sqlalchemy as sa

from alembic import op

revision = "20260920_0003"
down_revision = "20260918_0002"
branch_labels = None
depends_on = None

# (table, column, fk-constraint-name, was_not_null). The names follow the project's
# metadata naming convention: fk_<table>_<column>_<referred_table>.
FKS = [
    ("activities", "created_by", "fk_activities_created_by_users", True),
    ("activities", "verified_by", "fk_activities_verified_by_users", False),
    ("activity_evidence", "uploaded_by", "fk_activity_evidence_uploaded_by_users", True),
    ("activity_reviews", "reviewer_id", "fk_activity_reviews_reviewer_id_users", True),
    ("activity_revisions", "changed_by", "fk_activity_revisions_changed_by_users", True),
    ("audit_logs", "actor_user_id", "fk_audit_logs_actor_user_id_users", False),
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # Test/dev SQLite builds its schema from the models via create_all, so there is
        # nothing to alter here.
        return
    for table, column, fk, was_not_null in FKS:
        if was_not_null:
            op.alter_column(table, column, existing_type=sa.Uuid(), nullable=True)
        op.drop_constraint(fk, table, type_="foreignkey")
        op.create_foreign_key(fk, table, "users", [column], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    for table, column, fk, was_not_null in FKS:
        op.drop_constraint(fk, table, type_="foreignkey")
        op.create_foreign_key(fk, table, "users", [column], ["id"])
        if was_not_null:
            op.alter_column(table, column, existing_type=sa.Uuid(), nullable=False)
