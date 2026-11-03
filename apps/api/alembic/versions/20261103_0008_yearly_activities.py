"""Shared regional yearly identities, snapshotted DCU runs and ALC decisions.

Revision ID: 20261103_0008
Revises: 20261002_0007

Additive only: no existing tables/data are changed and no activities are seeded. The initial
revision creates current metadata; checkfirst preserves that existing repository convention.
Downgrade removes only this module's three tables and destroys its history: backup first.
"""
import sqlalchemy as sa

from alembic import op

revision = "20261103_0008"
down_revision = "20261002_0007"
branch_labels = None
depends_on = None

DEFINITIONS = "yearly_activity_definitions"
RUNS = "yearly_activity_dcu_runs"
COMPLETIONS = "yearly_activity_completions"


def _timestamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def _actor(name):
    return sa.Column(name, sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"))


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if DEFINITIONS not in tables:
        op.create_table(
            DEFINITIONS,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("rcu_id", sa.Uuid(), sa.ForeignKey("rcus.id", ondelete="RESTRICT"),
                      nullable=False),
            sa.Column("calendar_year", sa.Integer(), nullable=False),
            sa.Column("activity_code", sa.String(64), nullable=False),
            sa.Column("name", sa.String(180), nullable=False),
            sa.Column("description", sa.Text()),
            sa.Column("introduced_by_dcu_id", sa.Uuid(),
                      sa.ForeignKey("dcus.id", ondelete="RESTRICT"), nullable=False),
            _actor("created_by"), _actor("updated_by"), *_timestamps(),
            sa.UniqueConstraint("rcu_id", "calendar_year", "activity_code"),
            sa.CheckConstraint("calendar_year BETWEEN 2000 AND 2100",
                               name=op.f("ck_yearly_activity_definitions_valid_year")),
            sa.CheckConstraint("activity_code = upper(trim(activity_code))",
                               name=op.f("ck_yearly_activity_definitions_normalized_code")),
        )
    if RUNS not in tables:
        op.create_table(
            RUNS,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("yearly_activity_id", sa.Uuid(),
                      sa.ForeignKey(f"{DEFINITIONS}.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("dcu_id", sa.Uuid(), sa.ForeignKey("dcus.id", ondelete="RESTRICT"),
                      nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("scheduled_start_date", sa.Date()),
            sa.Column("scheduled_end_date", sa.Date()),
            sa.Column("instructions", sa.Text()),
            _actor("activated_by"), sa.Column("activated_at", sa.DateTime(timezone=True)),
            _actor("finalized_by"), sa.Column("finalized_at", sa.DateTime(timezone=True)),
            _actor("created_by"), _actor("updated_by"), *_timestamps(),
            sa.UniqueConstraint("yearly_activity_id", "dcu_id"),
            sa.UniqueConstraint("id", "yearly_activity_id"),
            sa.CheckConstraint(
                "status IN ('AVAILABLE','SCHEDULED','ACTIVE','MARKING','FINALIZED')",
                name=op.f("ck_yearly_activity_dcu_runs_valid_status")),
            sa.CheckConstraint(
                "(scheduled_start_date IS NULL AND scheduled_end_date IS NULL) OR "
                "(scheduled_start_date IS NOT NULL AND scheduled_end_date IS NOT NULL "
                "AND scheduled_end_date >= scheduled_start_date)",
                name=op.f("ck_yearly_activity_dcu_runs_schedule_order")),
            sa.CheckConstraint("status = 'AVAILABLE' OR scheduled_start_date IS NOT NULL",
                               name=op.f("ck_yearly_activity_dcu_runs_scheduled_state")),
            sa.CheckConstraint(
                "(status = 'FINALIZED' AND finalized_at IS NOT NULL) OR "
                "(status <> 'FINALIZED' AND finalized_at IS NULL)",
                name=op.f("ck_yearly_activity_dcu_runs_finalized_state")),
        )
        op.create_index("ix_yearly_activity_dcu_runs_yearly_activity_id", RUNS,
                        ["yearly_activity_id"])
        op.create_index("ix_yearly_activity_dcu_runs_dcu_id", RUNS, ["dcu_id"])
    if COMPLETIONS not in tables:
        op.create_table(
            COMPLETIONS,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("yearly_activity_id", sa.Uuid(),
                      sa.ForeignKey(f"{DEFINITIONS}.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("yearly_activity_dcu_run_id", sa.Uuid(), nullable=False),
            sa.Column("alc_id", sa.Uuid(), sa.ForeignKey("alcs.id", ondelete="RESTRICT"),
                      nullable=False),
            sa.Column("snapshot_sbu_id", sa.Uuid(),
                      sa.ForeignKey("sbus.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("completed", sa.Boolean()),
            _actor("marked_by"), sa.Column("marked_at", sa.DateTime(timezone=True)),
            _actor("updated_by"), *_timestamps(),
            sa.UniqueConstraint("yearly_activity_id", "alc_id"),
            sa.UniqueConstraint("yearly_activity_dcu_run_id", "alc_id"),
            sa.ForeignKeyConstraint(
                ["yearly_activity_dcu_run_id", "yearly_activity_id"],
                [f"{RUNS}.id", f"{RUNS}.yearly_activity_id"], ondelete="RESTRICT"),
        )
        for field in ("yearly_activity_id", "yearly_activity_dcu_run_id", "alc_id"):
            op.create_index(f"ix_yearly_activity_completions_{field}", COMPLETIONS, [field])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for name in (COMPLETIONS, RUNS, DEFINITIONS):
        if name in tables:
            op.drop_table(name)
