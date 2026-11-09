"""Scope yearly activity identity to its originating DCU.

Revision ID: 20261107_0012
Revises: 20261107_0011

All existing definitions already have a non-null introduced_by_dcu_id, so this migration
changes only the uniqueness boundary.  It does not rewrite ownership or activity data.
"""

import sqlalchemy as sa

from alembic import op

revision = "20261107_0012"
down_revision = "20261107_0011"
branch_labels = None
depends_on = None

TABLE = "yearly_activity_definitions"
NEW = "uq_yearly_activity_definition_dcu_year_code"
OLD_COLUMNS = {"rcu_id", "calendar_year", "activity_code"}
NEW_COLUMNS = {"introduced_by_dcu_id", "calendar_year", "activity_code"}


def _unique_name(columns: set[str]) -> str | None:
    for constraint in sa.inspect(op.get_bind()).get_unique_constraints(TABLE):
        if set(constraint["column_names"]) == columns and constraint.get("name"):
            return constraint["name"]
    return None


def upgrade() -> None:
    old = _unique_name(OLD_COLUMNS)
    new = _unique_name(NEW_COLUMNS)
    if new:
        # Fresh installations build current metadata in 0001.  If both constraints
        # exist, retain the intended DCU boundary and remove only the obsolete one.
        if old:
            op.drop_constraint(old, TABLE, type_="unique")
        return
    if not old:
        raise RuntimeError(
            "yearly_activity_definitions has neither the legacy RCU-scoped nor "
            "the expected DCU-scoped unique constraint"
        )
    op.drop_constraint(old, TABLE, type_="unique")
    op.create_unique_constraint(
        NEW, TABLE, ["introduced_by_dcu_id", "calendar_year", "activity_code"]
    )


def downgrade() -> None:
    old = _unique_name(OLD_COLUMNS)
    new = _unique_name(NEW_COLUMNS)
    if old:
        if new:
            op.drop_constraint(new, TABLE, type_="unique")
        return
    if not new:
        raise RuntimeError(
            "yearly_activity_definitions has neither the DCU-scoped nor legacy "
            "RCU-scoped unique constraint"
        )
    duplicate = op.get_bind().execute(
        sa.text(
            "SELECT rcu_id, calendar_year, activity_code "
            f"FROM {TABLE} GROUP BY rcu_id, calendar_year, activity_code "
            "HAVING count(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate:
        raise RuntimeError(
            "Cannot downgrade yearly activity uniqueness: multiple DCUs use the same "
            "activity code in one RCU/year"
        )
    op.drop_constraint(new, TABLE, type_="unique")
    op.create_unique_constraint(
        "uq_yearly_activity_definition_rcu_year_code",
        TABLE,
        ["rcu_id", "calendar_year", "activity_code"],
    )
