"""partial index for newest-first submitted-workflow activity lists

Revision ID: 20260930_0006
Revises: 20260924_0005
Create Date: 2026-09-30

Adds ``ix_activities_submitted_nondraft ON activities (submitted_at DESC)
WHERE status <> 'DRAFT'``.

Why: the Admin / DCU / SBU activity lists and review queues and the supervisor "recent
activity" query all read ``WHERE status <> 'DRAFT' ORDER BY submitted_at DESC, updated_at
DESC, id ...``. A draft has no ``submitted_at`` and NULLs sort first in DESC order, so with
only the plain ``ix_activities_submitted`` every page first walks past every draft. The partial
index holds no drafts, so a page reads only the rows it returns (plus its OFFSET).

Deployment: the index is built with ``CREATE INDEX CONCURRENTLY`` (outside the migration
transaction, via Alembic's autocommit block) so ``activities`` stays writable during the
build. ``CONCURRENTLY`` must not run inside a transaction, so this revision commits any
earlier revisions of the same ``alembic upgrade`` run before it builds the index.

Idempotent, like ``20260923_0004``: the initial revision builds a brand-new database from the
current models (which already declare this index), so an existing valid index is left alone.
An invalid leftover from an interrupted concurrent build is dropped and rebuilt.
"""
import sqlalchemy as sa

from alembic import op

revision = "20260930_0006"
down_revision = "20260924_0005"
branch_labels = None
depends_on = None

INDEX = "ix_activities_submitted_nondraft"
TABLE = "activities"
COLUMNS = [sa.text("submitted_at DESC")]
PREDICATE = sa.text("status <> 'DRAFT'")


def _postgres_index_valid(bind) -> bool | None:
    """True / False for a valid / invalid existing index, None when it does not exist."""
    return bind.execute(
        sa.text(
            "SELECT i.indisvalid FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relname = :name AND n.nspname = current_schema()"
        ),
        {"name": INDEX},
    ).scalar()


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # Non-PostgreSQL databases have no partial / concurrent index support to rely on.
        if INDEX not in {ix["name"] for ix in sa.inspect(bind).get_indexes(TABLE)}:
            op.create_index(INDEX, TABLE, COLUMNS)
        return
    valid = _postgres_index_valid(bind)
    if valid:
        return
    with op.get_context().autocommit_block():
        if valid is False:
            op.drop_index(INDEX, table_name=TABLE, postgresql_concurrently=True, if_exists=True)
        op.create_index(
            INDEX, TABLE, COLUMNS, postgresql_where=PREDICATE, postgresql_concurrently=True
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        if INDEX in {ix["name"] for ix in sa.inspect(bind).get_indexes(TABLE)}:
            op.drop_index(INDEX, table_name=TABLE)
        return
    with op.get_context().autocommit_block():
        op.drop_index(INDEX, table_name=TABLE, postgresql_concurrently=True, if_exists=True)
