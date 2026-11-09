"""Add evidence-based submissions without altering legacy participant/decision history."""
from alembic import op
from app.models import YearlyActivityEvidence, YearlyActivityReview, YearlyActivitySubmission

revision = "20261104_0010"
down_revision = "20261103_0009"
branch_labels = None
depends_on = None
TABLES = (YearlyActivitySubmission.__table__, YearlyActivityEvidence.__table__,
          YearlyActivityReview.__table__)


def upgrade():
    for table in TABLES:
        table.create(op.get_bind(), checkfirst=True)


def downgrade():
    for table in reversed(TABLES):
        table.drop(op.get_bind(), checkfirst=True)
