"""Optional participant-reported model attribution for numerical results.

Revision ID: 0010
Revises: 0009
Existing results have no inferred attribution; retry commitments stay intact.
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("submissions", sa.Column("ai_model", sa.String(300), nullable=True))


def downgrade():
    op.drop_column("submissions", "ai_model")
