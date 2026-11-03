"""Persistent device sessions referenced by access JWTs and rotating refresh tokens.

Revision ID: 20261103_0009
Revises: 20261103_0008

No existing rows are rewritten. Legacy credentials without a family fail closed in code.
The initial migration creates current metadata, so fresh-install paths use checkfirst.
"""
import sqlalchemy as sa

from alembic import op
from app.models import AuthSession

revision = "20261103_0009"
down_revision = "20261103_0008"
branch_labels = None
depends_on = None
FK = "fk_refresh_tokens_session_id_auth_sessions"
INDEX = "ix_refresh_tokens_session_id"


def upgrade() -> None:
    bind = op.get_bind()
    AuthSession.__table__.create(bind, checkfirst=True)
    if "session_id" not in {c["name"] for c in sa.inspect(bind).get_columns("refresh_tokens")}:
        with op.batch_alter_table("refresh_tokens") as batch:
            batch.add_column(sa.Column("session_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(FK, "auth_sessions", ["session_id"], ["id"],
                                     ondelete="CASCADE")
            batch.create_index(INDEX, ["session_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if "session_id" in {c["name"] for c in sa.inspect(bind).get_columns("refresh_tokens")}:
        with op.batch_alter_table("refresh_tokens") as batch:
            batch.drop_index(INDEX)
            batch.drop_constraint(FK, type_="foreignkey")
            batch.drop_column("session_id")
    AuthSession.__table__.drop(bind, checkfirst=True)
