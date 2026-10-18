"""Moderation withdrawals preserve immutable note and publication history."""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("notebook_publication_withdrawals",
        sa.Column("revision_id", sa.String(36), sa.ForeignKey("notebook_revisions.id"), primary_key=True),
        sa.Column("moderator_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(10000), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint("moderator_id", "idempotency_key", name="uq_notebook_withdrawal_key"))


def downgrade():
    op.drop_table("notebook_publication_withdrawals")
