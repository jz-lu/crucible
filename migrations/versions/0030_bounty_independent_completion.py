"""Allow independent-reviewer bounty awards; preserve historical signoffs.

Revision ID: 0030
Revises: 0029
"""
from alembic import op
import sqlalchemy as sa

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("bounty_awards") as batch:
        batch.alter_column("poster_decision_id", existing_type=sa.String(36), nullable=True)


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM bounty_awards WHERE poster_decision_id IS NULL")):
        raise RuntimeError("Preserve independent-reviewer bounty awards before downgrading schema0030")
    with op.batch_alter_table("bounty_awards") as batch:
        batch.alter_column("poster_decision_id", existing_type=sa.String(36), nullable=False)
